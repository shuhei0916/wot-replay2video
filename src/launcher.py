"""
WoT クライアントを起動してリプレイを再生する。
WSL2 から Windows の WorldOfTanks.exe を呼び出す。
"""

import subprocess
import sys
import time
from pathlib import Path

WOT_DIR = Path("/mnt/x/Games/World_of_Tanks_ASIA")
WOT_EXE = WOT_DIR / "WorldOfTanks.exe"
WOT_LOG = WOT_DIR / "python.log"


def wsl_to_win(path: Path) -> str:
    """WSL パスを Windows パス文字列に変換する。"""
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def is_wot_running() -> bool:
    result = subprocess.run(
        ["cmd.exe", "/c", "tasklist /fi \"imagename eq WorldOfTanks.exe\" /fo csv /nh"],
        capture_output=True, text=True
    )
    return "WorldOfTanks.exe" in result.stdout


def kill_wot() -> None:
    """実行中の WoT を強制終了する。"""
    subprocess.run(
        ["cmd.exe", "/c", "taskkill /IM WorldOfTanks.exe /F"],
        capture_output=True
    )
    # プロセスが消えるまで待つ
    for _ in range(10):
        if not is_wot_running():
            return
        time.sleep(1)


def _log_session_start() -> int:
    """現在の python.log のバイト数を返す（新セッション検出の基準）。"""
    try:
        return WOT_LOG.stat().st_size
    except FileNotFoundError:
        return 0


def wait_for_replay_start(log_offset: int, timeout: int = 120) -> bool:
    """
    リプレイ再生開始（BattleLoadingSpace）を python.log で検出する。

    Args:
        log_offset: 起動前の python.log バイト数
        timeout: 最大待機秒数

    Returns:
        タイムアウト前に検出できた場合 True
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(WOT_LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(log_offset)
                content = f.read()
            if "BattleLoadingSpace" in content:
                return True
        except OSError:
            pass
        time.sleep(2)
    return False


def wait_for_replay_end(log_offset: int, timeout: int = 900) -> bool:
    """
    リプレイ終了（onReplayTerminated）を python.log で検出する。

    Args:
        log_offset: 起動前の python.log バイト数
        timeout: 最大待機秒数（デフォルト15分）

    Returns:
        タイムアウト前に検出できた場合 True
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(WOT_LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(log_offset)
                content = f.read()
            if "onReplayTerminated" in content:
                return True
        except OSError:
            pass
        time.sleep(3)
    return False


def launch_replay(replay_path: Path, wait: bool = False) -> subprocess.Popen:
    """
    WoT を起動して replay_path のリプレイを再生する。

    Args:
        replay_path: .wotreplay ファイルの WSL パス
        wait: True なら再生終了まで待機する

    Returns:
        起動した Popen オブジェクト
    """
    replay_path = Path(replay_path).resolve()
    if not replay_path.exists():
        raise FileNotFoundError(f"リプレイが見つかりません: {replay_path}")
    if not WOT_EXE.exists():
        raise FileNotFoundError(f"WorldOfTanks.exe が見つかりません: {WOT_EXE}")

    if is_wot_running():
        print("既存の WoT プロセスを終了します...")
        kill_wot()

    win_replay = wsl_to_win(replay_path)

    print(f"起動: {wsl_to_win(WOT_EXE)}")
    print(f"リプレイ: {win_replay}")

    log_offset = _log_session_start()

    # WSL2 から Windows exe を直接起動。cwd に WSL パスを渡すと Windows パスに変換される。
    proc = subprocess.Popen(
        [str(WOT_EXE), win_replay],
        cwd=str(WOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if wait:
        print("リプレイ開始を待機中...")
        if wait_for_replay_start(log_offset):
            print("リプレイ再生中...")
        else:
            print("警告: リプレイ開始の検出がタイムアウトしました")

        print("リプレイ終了を待機中...")
        if wait_for_replay_end(log_offset):
            print("リプレイ終了を検出しました")
        else:
            print("警告: リプレイ終了の検出がタイムアウトしました")

    return proc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        project_root = Path(__file__).parent.parent
        replays = sorted((project_root / "replays").glob("*.wotreplay"))
        if not replays:
            print("使い方: python launcher.py <replay.wotreplay>")
            sys.exit(1)
        replay = replays[0]
    else:
        replay = Path(sys.argv[1])

    print(f"リプレイ: {replay.name}")
    proc = launch_replay(replay, wait=True)
    print(f"完了 (PID: {proc.pid})")
