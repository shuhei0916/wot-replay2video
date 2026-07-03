"""
WoT クライアントを起動してリプレイを再生する。
Windows ネイティブ Python から直接 WorldOfTanks.exe を呼び出す。

python.log の監視はバイトオフセットではなく行タイムスタンプで行う。
WoT は python.log を追記するが、容量上限で起動時に切り詰めることが
あり、オフレット基準だと切り詰め後にマーカーを永遠に見逃す。
"""

import datetime
import re
import subprocess
import sys
import time
from pathlib import Path

from src.config import load_config

_wot_cfg = load_config().get("wot", {})

WOT_DIR = Path(_wot_cfg.get("dir", r"C:\Games\World_of_Tanks_ASIA"))
WOT_EXE = WOT_DIR / "WorldOfTanks.exe"
WOT_LOG = WOT_DIR / "python.log"


def is_wot_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/fo", "csv", "/nh"],
        capture_output=True,
    )
    return b"WorldOfTanks.exe" in result.stdout


def kill_wot() -> None:
    """
    実行中の全 WoT プロセスを強制終了し、完全消滅を確認してから待機する。
    複数インスタンスが残っているとミューテックス競合でクラッシュするため、
    全プロセス消滅 + 追加 sleep で確実にクリーンな状態にする。
    """
    if not is_wot_running():
        return

    for _ in range(2):
        subprocess.run(
            ["taskkill", "/IM", "WorldOfTanks.exe", "/F"],
            capture_output=True,
        )
        time.sleep(1)

    for _ in range(20):
        if not is_wot_running():
            break
        time.sleep(1)

    time.sleep(5)


def _wot_pids() -> set[int]:
    """実行中の WorldOfTanks.exe の PID 一覧を返す。"""
    result = subprocess.run(
        ["tasklist", "/fi", "imagename eq WorldOfTanks.exe", "/fo", "csv", "/nh"],
        capture_output=True, text=True,
    )
    pids = set()
    for line in result.stdout.splitlines():
        parts = line.split('","')
        if len(parts) >= 2 and "WorldOfTanks" in parts[0]:
            try:
                pids.add(int(parts[1].strip('"')))
            except ValueError:
                pass
    return pids


def _find_wot_hwnd() -> int | None:
    """WoT プロセスの可視トップレベルウィンドウのハンドルを返す。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pids = _wot_pids()
    if not pids:
        return None

    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def is_wot_foreground() -> bool:
    """フォアグラウンドウィンドウが WoT かどうか。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value in _wot_pids()


def bring_wot_to_foreground(timeout: int = 60) -> bool:
    """
    WoT ウィンドウを前面に出す。

    バックグラウンドプロセス（自動化スクリプト）から起動した WoT は
    Windows のフォアグラウンドロックにより背面に回ることがある。
    背面のままだと画面キャプチャ録画に別のウィンドウが映ってしまう。
    ALT キーイベントでロックを解除してから SetForegroundWindow する。
    """
    import ctypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x2

    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _find_wot_hwnd()
        if hwnd:
            if is_wot_foreground():
                return True
            # ALT 押下でフォアグラウンドロックを解除する定番ワークアラウンド
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            time.sleep(1)
            if is_wot_foreground():
                return True
            # 最後の手段（古い API だが効果が高い）
            user32.SwitchToThisWindow(hwnd, True)
            time.sleep(1)
            if is_wot_foreground():
                return True
        time.sleep(2)
    return False


_LOG_TS_RE = re.compile(rb"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+:")


def find_marker_since(data: bytes, marker: bytes, since: "datetime.datetime") -> int | None:
    """
    ログ内容 data から、since 以降のタイムスタンプ行にある marker を探す。

    Returns:
        マーカー直後のバイト位置。見つからなければ None。
    """
    pos = 0
    while True:
        idx = data.find(marker, pos)
        if idx < 0:
            return None
        line_start = data.rfind(b"\n", 0, idx) + 1
        m = _LOG_TS_RE.match(data[line_start:line_start + 40])
        if m:
            try:
                ts = datetime.datetime.strptime(
                    m.group(1).decode(), "%Y-%m-%d %H:%M:%S"
                )
                if ts >= since:
                    return idx + len(marker)
            except ValueError:
                pass
        pos = idx + len(marker)


def wait_for_replay_start(launched_at: float, timeout: int = 180) -> int:
    """
    リプレイ再生開始（BattleLoadingSpace）を python.log で検出する。

    launched_at（エポック秒）より新しいタイムスタンプの行だけを対象に
    するため、過去セッションの残骸やログの切り詰めに影響されない。

    Args:
        launched_at: WoT を起動したエポック秒（launch_replay の戻り値）
        timeout: 最大待機秒数

    Returns:
        BattleLoadingSpace 検出後のログオフセット（タイムアウト時は 0）
    """
    deadline = time.time() + timeout
    since = datetime.datetime.fromtimestamp(launched_at - 2)
    while time.time() < deadline:
        if not is_wot_running():
            return 0
        try:
            data = WOT_LOG.read_bytes()
        except OSError:
            data = b""
        pos = find_marker_since(data, b"BattleLoadingSpace", since)
        if pos is not None:
            return pos
        time.sleep(2)
    return 0


def wait_for_replay_end(log_offset: int, timeout: int = 900) -> bool:
    """
    リプレイ終了を python.log で検出する。

    コマンドライン起動のリプレイは onReplayTerminated の前に
    "simpleDialog name=rw1"（ゲーム終了ダイアログ）が出る。
    どちらかを検出したら終了とみなす。

    Args:
        log_offset: 起動前の python.log バイト数
        timeout: 最大待機秒数（デフォルト15分）

    Returns:
        タイムアウト前に検出できた場合 True
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_wot_running():
            return True
        try:
            with open(WOT_LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(log_offset)
                content = f.read()
            if "onReplayTerminated" in content or 'name=rw1' in content:
                return True
        except OSError:
            pass
        time.sleep(3)
    return False


def launch_replay(replay_path: Path, wait: bool = False) -> tuple:
    """
    WoT を起動して replay_path のリプレイを再生する。

    Args:
        replay_path: .wotreplay ファイルの Windows パス
        wait: True なら再生終了まで待機する

    Returns:
        (起動した Popen オブジェクト, 起動エポック秒)
        起動エポック秒は wait_for_replay_start() にそのまま渡す
    """
    replay_path = Path(replay_path).resolve()
    if not replay_path.exists():
        raise FileNotFoundError(f"リプレイが見つかりません: {replay_path}")
    if not WOT_EXE.exists():
        raise FileNotFoundError(f"WorldOfTanks.exe が見つかりません: {WOT_EXE}")

    if is_wot_running():
        print("既存の WoT プロセスを終了します...")
        kill_wot()

    print(f"起動: {WOT_EXE}")
    print(f"リプレイ: {replay_path}")

    launched_at = time.time()

    proc = subprocess.Popen(
        [str(WOT_EXE), str(replay_path)],
        cwd=str(WOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(30):
        if is_wot_running():
            break
        time.sleep(1)
    else:
        raise RuntimeError("WoT プロセスが起動しませんでした")

    if wait:
        print("リプレイ開始を待機中...")
        battle_offset = wait_for_replay_start(launched_at)
        if battle_offset:
            print("リプレイ再生中...")
        else:
            print("警告: リプレイ開始の検出がタイムアウトしました")
            battle_offset = 0

        print("リプレイ終了を待機中...")
        if wait_for_replay_end(battle_offset):
            print("リプレイ終了を検出しました")
        else:
            print("警告: リプレイ終了の検出がタイムアウトしました")

    return proc, launched_at


if __name__ == "__main__":
    if len(sys.argv) < 2:
        project_root = Path(__file__).parent.parent
        replays = sorted((project_root / "replays").glob("*.wotreplay"))
        if not replays:
            print("使い方: python -m src.launcher <replay.wotreplay>")
            sys.exit(1)
        replay = replays[0]
    else:
        replay = Path(sys.argv[1])

    print(f"リプレイ: {replay.name}")
    proc, _ = launch_replay(replay, wait=True)
    print(f"完了 (PID: {proc.pid})")
