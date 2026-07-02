"""
リプレイ再生 + 録画のパイプライン。
launcher と recorder を組み合わせて一連の処理を実行する。
"""

import datetime
import subprocess
import sys
import time
from pathlib import Path

import yaml

from src.launcher import launch_replay, wait_for_replay_start, wait_for_replay_end, kill_wot
from src.recorder import start_recording, stop_recording, OUTPUT_DIR
from src.detect_highlights import detect_highlights
from src.edit_video import make_shorts
from src.upload_youtube import upload_video

_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"

FFMPEG_CANDIDATES = [
    "ffmpeg",
    r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe",
]


def _find_ffmpeg() -> str | None:
    for c in FFMPEG_CANDIDATES:
        try:
            r = subprocess.run([c, "-version"], capture_output=True)
            if r.returncode == 0:
                return c
        except FileNotFoundError:
            pass
    return None


def _remux_faststart(src: Path) -> Path:
    """mp4 を上書きリムックスして moov アトムを先頭に移動する（seekable 化）。"""
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        print("警告: ffmpeg が見つかりません。シーク不可のまま出力します。")
        return src

    tmp = src.with_suffix(".tmp.mp4")
    subprocess.run(
        [ffmpeg, "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(tmp), "-y"],
        check=True,
        capture_output=True,
    )
    tmp.replace(src)
    return src


def record_replay(replay_path: Path) -> Path:
    """
    リプレイを再生しながら OBS で録画して、動画ファイルパスを返す。

    録画の解像度・フレームレート・音声は OBS 側で設定する。

    Args:
        replay_path: .wotreplay ファイルの Windows パス

    Returns:
        録画された動画ファイルのパス（seekable MP4）
    """
    replay_path = Path(replay_path).resolve()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"{replay_path.stem}_{ts}.mp4"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] WoT 起動中: {replay_path.name}")
    wot_proc, log_offset = launch_replay(replay_path)

    try:
        print("[2/5] リプレイ開始を待機中...")
        battle_log_offset = wait_for_replay_start(log_offset, timeout=300)
        if not battle_log_offset:
            raise TimeoutError("リプレイ開始の検出がタイムアウトしました")

        print(f"[3/5] 録画開始 → {out_path.name}")
        rec_client = start_recording()

        print("[4/5] リプレイ終了を待機中...")
        if not wait_for_replay_end(battle_log_offset, timeout=900):
            print("警告: リプレイ終了の検出がタイムアウトしました（強制終了）")

        print("[5/5] 録画停止・WoT 終了...")
        stop_recording(rec_client, out_path)
    finally:
        kill_wot()

    print("リムックス中（seekable 化）...")
    _remux_faststart(out_path)

    print("ハイライト検出中...")
    events = detect_highlights(out_path)
    print(f"  {len(events)} 件のショットイベントを検出")

    if not events:
        print("ハイライトが見つかりませんでした。録画のみ保存します。")
        print(f"完了（録画のみ）: {out_path}")
        return out_path

    shorts_path = out_path.with_name(out_path.stem + "_shorts.mp4")
    print(f"Shorts 生成中 → {shorts_path.name}")
    make_shorts(out_path, events, shorts_path)

    print(f"完了: {shorts_path}")
    _try_upload(shorts_path, replay_path)
    return shorts_path


def _try_upload(video_path: Path, replay_path: Path) -> None:
    """Shorts を YouTube にアップロードする。失敗してもパイプラインは継続する。"""
    try:
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        yt = cfg.get("youtube", {})
        privacy = yt.get("privacy", "private")
        category_id = yt.get("category_id", "20")
        default_tags = yt.get("default_tags", [])

        from src.parse_replay import parse_replay, generate_title
        try:
            info = parse_replay(replay_path)
            title = generate_title(info)
        except Exception:
            title = f"【WoT】{replay_path.stem} #Shorts #WorldOfTanks"

        upload_video(
            video_path=video_path,
            title=title,
            privacy=privacy,
            category_id=category_id,
            extra_tags=default_tags,
        )
    except Exception as e:
        print(f"警告: YouTube アップロードに失敗しました（動画は保持）: {e}")


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
