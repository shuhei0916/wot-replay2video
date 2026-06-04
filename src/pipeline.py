"""
リプレイ再生 + 録画のパイプライン。
launcher と recorder を組み合わせて一連の処理を実行する。
"""

import datetime
import sys
import time
from pathlib import Path

from src.launcher import launch_replay, wait_for_replay_start, wait_for_replay_end, WOT_LOG
from src.recorder import start_recording, stop_recording, find_ffmpeg, OUTPUT_DIR


def record_replay(
    replay_path: Path,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    offset_x: int = 0,
    offset_y: int = 0,
) -> Path:
    """
    リプレイを再生しながら録画して、動画ファイルパスを返す。

    Args:
        replay_path: .wotreplay ファイルの WSL パス
        fps: 録画フレームレート
        width/height: キャプチャ解像度（WoT の画面解像度に合わせる）
        offset_x/y: キャプチャ開始座標（デュアルモニター環境用）

    Returns:
        録画された動画ファイルの WSL パス
    """
    replay_path = Path(replay_path).resolve()

    if find_ffmpeg() is None:
        raise RuntimeError("ffmpeg.exe が見つかりません")

    # 出力ファイル名をリプレイ名から生成
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"{replay_path.stem}_{ts}.mp4"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] WoT 起動中: {replay_path.name}")
    # log_offset は launch_replay 内部で kill 後に計測し、タプルで返す
    wot_proc, log_offset = launch_replay(replay_path)

    print("[2/4] リプレイ開始を待機中...")
    if not wait_for_replay_start(log_offset, timeout=120):
        wot_proc.kill()
        raise TimeoutError("リプレイ開始の検出がタイムアウトしました")

    # 録画開始（少し余裕を持たせてバトルロード画面も含める）
    print(f"[3/4] 録画開始 → {out_path.name}")
    rec_proc = start_recording(out_path, fps=fps, width=width, height=height,
                               offset_x=offset_x, offset_y=offset_y)

    # リプレイ終了を待機
    print("[4/4] リプレイ終了を待機中...")
    if not wait_for_replay_end(log_offset, timeout=900):
        print("警告: リプレイ終了の検出がタイムアウトしました（強制終了）")

    # 録画停止（ffmpeg に q を送って正常終了）
    print("録画停止中...")
    stop_recording(rec_proc)

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
