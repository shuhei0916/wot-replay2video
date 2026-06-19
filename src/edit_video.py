"""
ハイライトイベントから YouTube Shorts 用動画を生成する。

処理フロー:
  1. イベントタイムスタンプ周辺をクリップ
  2. 各クリップを 9:16 縦型にクロップ（中央）
  3. クリップを結合して最大 60 秒の Shorts 動画を出力
"""

import subprocess
from pathlib import Path

from src.detect_highlights import HighlightEvent

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Shorts 仕様
SHORTS_MAX_SEC = 59      # YouTube Shorts 上限 60 秒（余裕を 1 秒持たせる）
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
CLIP_PRE_SEC = 3.0       # イベント前の余白
CLIP_POST_SEC = 4.0      # イベント後の余白


def _find_ffmpeg() -> str:
    """使用可能な ffmpeg バイナリパスを返す。"""
    candidates = [
        "ffmpeg",
        r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe",
    ]
    for c in candidates:
        result = subprocess.run([c, "-version"], capture_output=True)
        if result.returncode == 0:
            return c
    raise RuntimeError("ffmpeg が見つかりません")


def clip_and_crop(
    video_path: Path,
    start: float,
    duration: float,
    output_path: Path,
    src_width: int = 1920,
    src_height: int = 1080,
) -> Path:
    """
    動画の指定区間を切り出し、中央を 9:16 にクロップして保存する。

    Args:
        video_path: 元動画パス
        start: 切り出し開始秒
        duration: 切り出し秒数
        output_path: 出力パス
        src_width/src_height: 元動画の解像度

    Returns:
        出力ファイルパス
    """
    ffmpeg = _find_ffmpeg()

    # 9:16 にするためのクロップサイズを計算
    # 元が 1920x1080 の場合: 高さ 1080 を基準に幅 = 1080 * 9/16 = 607
    crop_w = int(src_height * 9 / 16)
    crop_h = src_height
    crop_x = (src_width - crop_w) // 2
    crop_y = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            ffmpeg,
            "-ss", str(max(0, start)),
            "-i", str(video_path),
            "-t", str(duration),
            "-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-y",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )
    return output_path


def make_shorts(
    video_path: Path,
    events: list[HighlightEvent],
    output_path: Path | None = None,
    battle_start_offset: float = 0.0,
    battle_duration: float | None = None,
) -> Path:
    """
    ハイライトイベントから YouTube Shorts 動画を生成する。

    Args:
        video_path: 元の録画動画パス
        events: detect_highlights() が返したイベントリスト
        output_path: 出力先（None なら output/ 以下に自動生成）
        battle_start_offset: 動画内でバトルが始まる秒数（ローディング除外）
        battle_duration: バトルの秒数（リザルト画面を除外するため）

    Returns:
        生成した Shorts 動画のパス
    """
    if output_path is None:
        stem = video_path.stem
        output_path = OUTPUT_DIR / f"{stem}_shorts.mp4"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # バトル範囲内のイベントに絞る
    filtered = events
    if battle_duration is not None:
        battle_end = battle_start_offset + battle_duration
        filtered = [e for e in events if battle_start_offset <= e.timestamp <= battle_end]

    # スコア降順でソートし、合計 SHORTS_MAX_SEC 秒に収まる範囲で選択
    clip_duration = CLIP_PRE_SEC + CLIP_POST_SEC
    selected = sorted(filtered, key=lambda e: e.score, reverse=True)

    max_clips = int(SHORTS_MAX_SEC // clip_duration)
    selected = selected[:max_clips]

    if not selected:
        raise ValueError("選択されたハイライトイベントがありません")

    # タイムスタンプ順に並べ直す
    selected = sorted(selected, key=lambda e: e.timestamp)

    # 各クリップを生成
    clips_dir = OUTPUT_DIR / "clips"
    clips_dir.mkdir(exist_ok=True)
    clip_paths: list[Path] = []

    for i, event in enumerate(selected):
        start = event.timestamp - CLIP_PRE_SEC
        clip_out = clips_dir / f"clip_{i:03d}_{event.timestamp:.1f}s.mp4"
        clip_and_crop(video_path, start, clip_duration, clip_out)
        clip_paths.append(clip_out)
        print(f"  clip {i+1}/{len(selected)}: {event.timestamp:.1f}s (score={event.score:.3f}) → {clip_out.name}")

    # クリップリストファイル（ffmpeg concat 用）
    list_file = clips_dir / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in clip_paths) + "\n"
    )

    # クリップを結合
    ffmpeg = _find_ffmpeg()
    subprocess.run(
        [
            ffmpeg,
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-y",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )

    return output_path
