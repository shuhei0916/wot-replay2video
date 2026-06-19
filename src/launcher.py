"""
WoT クライアントを起動してリプレイを再生する。
Windows ネイティブ Python から直接 WorldOfTanks.exe を呼び出す。
"""

import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
    _cfg_path = Path(__file__).parent.parent / "config.yaml"
    _cfg = yaml.safe_load(_cfg_path.read_text()) if _cfg_path.exists() else {}
except Exception:
    _cfg = {}

_wot_cfg = _cfg.get("wot", {})

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


def wait_for_replay_start(log_offset: int, timeout: int = 180) -> bool:
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
        if not is_wot_running():
            return False
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
        (起動した Popen オブジェクト, log_offset)
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

    try:
        log_offset = WOT_LOG.stat().st_size
    except FileNotFoundError:
        log_offset = 0

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
        if wait_for_replay_start(log_offset):
            print("リプレイ再生中...")
        else:
            print("警告: リプレイ開始の検出がタイムアウトしました")

        print("リプレイ終了を待機中...")
        if wait_for_replay_end(log_offset):
            print("リプレイ終了を検出しました")
        else:
            print("警告: リプレイ終了の検出がタイムアウトしました")

    return proc, log_offset


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
    proc, _ = launch_replay(replay, wait=True)
    print(f"完了 (PID: {proc.pid})")
