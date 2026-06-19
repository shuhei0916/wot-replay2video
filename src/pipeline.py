"""
リプレイ再生 + 録画のパイプライン。
launcher と recorder を組み合わせて一連の処理を実行する。
"""

import datetime
import sys
import time
from pathlib import Path

from src.launcher import launch_replay, wait_for_replay_start, wait_for_replay_end, WOT_LOG
from src.recorder import start_recording, stop_recording, OUTPUT_DIR


def record_replay(replay_path: Path) -> Path:
    """
    リプレイを再生しながら OBS で録画して、動画ファイルパスを返す。

    録画の解像度・フレームレート・音声は OBS 側で設定する。

    Args:
        replay_path: .wotreplay ファイルの WSL パス

    Returns:
        録画された動画ファイルの WSL パス
    """
    replay_path = Path(replay_path).resolve()

    # 出力ファイル名をリプレイ名から生成
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"{replay_path.stem}_{ts}.mp4"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] WoT 起動中: {replay_path.name}")
    wot_proc, log_offset = launch_replay(replay_path)

    print("[2/4] リプレイ開始を待機中...")
    if not wait_for_replay_start(log_offset, timeout=120):
        wot_proc.kill()
        raise TimeoutError("リプレイ開始の検出がタイムアウトしました")

    print(f"[3/4] 録画開始 → {out_path.name}")
    rec_client = start_recording()

    print("[4/4] リプレイ終了を待機中...")
    if not wait_for_replay_end(log_offset, timeout=900):
        print("警告: リプレイ終了の検出がタイムアウトしました（強制終了）")

    print("録画停止中...")
    stop_recording(rec_client, out_path)

    print(f"完了: {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        project_root = Path(__file__).parent.parent
        replays = sorted((project_root / "replays").glob("*.wotreplay"))
        if not replays:
            print("使い方: python -m src.pipeline <replay.wotreplay>")
            sys.exit(1)
        replay = replays[0]
    else:
        replay = Path(sys.argv[1])

    out = record_replay(replay)
    print(f"\n動画ファイル: {out}")
