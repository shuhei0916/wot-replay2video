"""
リプレイ再生 + 録画 + Shorts 生成 + アップロードのパイプライン。

各ステージは独立した関数で、process_replay() がオーケストレーションする:
  record_replay()          リプレイを再生して録画（seekable MP4）
  make_highlight_shorts()  ハイライト検出 → Shorts 生成
  build_title()            リプレイ情報からタイトル生成
  upload_shorts()          YouTube アップロード（失敗しても継続）
"""

import datetime
import subprocess
import sys
from pathlib import Path

from src.config import OUTPUT_DIR, find_ffmpeg, load_config
from src.launcher import launch_replay, wait_for_replay_start, wait_for_replay_end, kill_wot
from src.recorder import start_recording, stop_recording
from src.detect_highlights import detect_highlights
from src.edit_video import make_shorts
from src.parse_replay import parse_replay, generate_title
from src.upload_youtube import upload_video


def _remux_faststart(src: Path) -> Path:
    """mp4 を上書きリムックスして moov アトムを先頭に移動する（seekable 化）。"""
    ffmpeg = find_ffmpeg()
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
    return _remux_faststart(out_path)


def make_highlight_shorts(recording_path: Path) -> Path | None:
    """
    録画からハイライトを検出して Shorts 動画を生成する。

    Returns:
        Shorts 動画のパス。ハイライトが見つからない場合は None。
    """
    print("ハイライト検出中...")
    events = detect_highlights(recording_path)
    print(f"  {len(events)} 件のショットイベントを検出")

    if not events:
        return None

    shorts_path = recording_path.with_name(recording_path.stem + "_shorts.mp4")
    print(f"Shorts 生成中 → {shorts_path.name}")
    make_shorts(recording_path, events, shorts_path)
    return shorts_path


def build_title(replay_path: Path) -> str:
    """リプレイのメタデータからタイトルを生成する。解析失敗時はファイル名ベース。"""
    try:
        return generate_title(parse_replay(replay_path))
    except Exception:
        return f"【WoT】{replay_path.stem} #Shorts #WorldOfTanks"


def upload_shorts(video_path: Path, title: str) -> None:
    """Shorts を YouTube にアップロードする。失敗してもパイプラインは継続する。"""
    try:
        yt = load_config().get("youtube", {})
        upload_video(
            video_path=video_path,
            title=title,
            privacy=yt.get("privacy", "private"),
            category_id=yt.get("category_id", "20"),
            extra_tags=yt.get("default_tags", []),
        )
    except Exception as e:
        print(f"警告: YouTube アップロードに失敗しました（動画は保持）: {e}")


def process_replay(replay_path: Path) -> Path:
    """
    1本のリプレイを録画 → Shorts 生成 → アップロードまで処理する。

    Returns:
        Shorts 動画のパス（ハイライトなしの場合は録画ファイルのパス）
    """
    replay_path = Path(replay_path)
    recording = record_replay(replay_path)

    shorts_path = make_highlight_shorts(recording)
    if shorts_path is None:
        print(f"ハイライトが見つかりませんでした。録画のみ保存: {recording}")
        return recording

    title = build_title(replay_path)
    title_path = shorts_path.with_suffix(".txt")
    title_path.write_text(title, encoding="utf-8")
    print(f"タイトル: {title}")

    upload_shorts(shorts_path, title)
    print(f"完了: {shorts_path}")
    return shorts_path


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

    out = process_replay(replay)
    print(f"\n動画ファイル: {out}")
