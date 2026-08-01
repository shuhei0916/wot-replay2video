"""
ハイライトイベントから YouTube Shorts 用動画を生成する。

処理フロー:
  1. イベントタイムスタンプ周辺をクリップ（重複除去済み）
  2. 各クリップを 9:16 縦型にクロップ（中央）
  3. クリップを結合して最大 60 秒の Shorts 動画を出力
"""

import subprocess
from pathlib import Path

from src.config import OUTPUT_DIR, find_ffmpeg, load_config
from src.detect_highlights import HighlightEvent

# Shorts 仕様
SHORTS_MAX_SEC = 150     # 動画の合計上限秒数（Shorts 自体は最大3分まで可）
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
CLIP_PRE_SEC = 3.0       # イベント前の余白
CLIP_POST_SEC = 4.0      # イベント後の余白


def _find_ffmpeg() -> str:
    """使用可能な ffmpeg バイナリパスを返す。見つからなければ RuntimeError。"""
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg が見つかりません")
    return ffmpeg


def _effective_pre(event: HighlightEvent) -> float:
    """イベントのクリップ前余白。可変長指定が無ければ既定値を使う。"""
    return event.pre_sec if event.pre_sec is not None else CLIP_PRE_SEC


def _effective_post(event: HighlightEvent) -> float:
    """イベントのクリップ後余白。可変長指定が無ければ既定値を使う。"""
    return event.post_sec if event.post_sec is not None else CLIP_POST_SEC


def _effective_duration(event: HighlightEvent) -> float:
    return _effective_pre(event) + _effective_post(event)


def _dedup_clips(events: list[HighlightEvent]) -> list[HighlightEvent]:
    """
    スコア降順で選択しながら、時間が重複するイベントを除去する。

    高スコアのイベントを優先し、そのクリップ範囲と重なる低スコアの
    イベントをスキップする。クリップ範囲は各イベント自身の pre_sec/post_sec
    （可変長クリップ）を使う。
    """
    by_score = sorted(events, key=lambda e: e.score, reverse=True)
    kept: list[HighlightEvent] = []
    for e in by_score:
        e_start = e.timestamp - _effective_pre(e)
        e_end   = e.timestamp + _effective_post(e)
        overlap = any(
            not (e_end <= (k.timestamp - _effective_pre(k)) or e_start >= (k.timestamp + _effective_post(k)))
            for k in kept
        )
        if not overlap:
            kept.append(e)
    return kept


def select_clips(
    events: list[HighlightEvent],
    max_total_sec: float = SHORTS_MAX_SEC,
) -> list[HighlightEvent]:
    """
    Shorts に収めるイベントを選択する。

    重複除去後、スコア降順に舐めながら、そのイベントの実尺（可変長）を
    足しても合計が max_total_sec を超えない限り採用する（超えるものは
    スキップして次を試す貪欲法）。採用したものをタイムスタンプ順に並べて返す。
    """
    deduped = _dedup_clips(events)
    by_score = sorted(deduped, key=lambda e: e.score, reverse=True)
    selected: list[HighlightEvent] = []
    total = 0.0
    for e in by_score:
        duration = _effective_duration(e)
        if total + duration <= max_total_sec:
            selected.append(e)
            total += duration
    if not selected and by_score:
        # 全イベントが単体で予算オーバーでも、最低1本は残す
        selected.append(by_score[0])
    return sorted(selected, key=lambda e: e.timestamp)


def hp_overlay_config() -> dict | None:
    """config.yaml の shorts.hp_overlay を返す。無効・未設定なら None。"""
    cfg = (load_config().get("shorts") or {}).get("hp_overlay") or {}
    if not cfg.get("enabled"):
        return None
    return cfg


def build_filter_args(
    src_width: int,
    src_height: int,
    hp_overlay: dict | None = None,
) -> list[str]:
    """
    クリップ切り出し用の ffmpeg フィルタ引数を組み立てる。

    基本は中央 9:16 クロップ + 拡大。hp_overlay が指定されていれば、
    元フレーム左下 HUD の HP バー矩形（src）を切り出して縦動画上部
    （dst: 幅 w で中央寄せ、上端 y）に重ねる filter_complex になる。
    HP バーは毎フレーム元映像から取るので、被弾によるバーの変化も映る。
    """
    crop_w = int(src_height * 9 / 16)
    crop_h = src_height
    crop_x = (src_width - crop_w) // 2
    base = (
        f"crop={crop_w}:{crop_h}:{crop_x}:0,"
        f"scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}"
    )
    if hp_overlay is None:
        return ["-vf", base]

    s, d = hp_overlay["src"], hp_overlay["dst"]
    dst_w = d["w"]
    dst_h = round(s["h"] * dst_w / s["w"])  # アスペクト比維持
    dst_x = (SHORTS_WIDTH - dst_w) // 2
    return [
        "-filter_complex",
        (
            f"[0:v]split=2[main][hud];"
            f"[main]{base}[bg];"
            f"[hud]crop={s['w']}:{s['h']}:{s['x']}:{s['y']},"
            f"scale={dst_w}:{dst_h}:flags=lanczos[bar];"
            f"[bg][bar]overlay={dst_x}:{d['y']}"
        ),
    ]


def clip_and_crop(
    video_path: Path,
    start: float,
    duration: float,
    output_path: Path,
    src_width: int = 1920,
    src_height: int = 1080,
    hp_overlay: dict | None = None,
) -> Path:
    """
    動画の指定区間を切り出し、中央を 9:16 にクロップして保存する。

    Args:
        video_path: 元動画パス
        start: 切り出し開始秒
        duration: 切り出し秒数
        output_path: 出力パス
        src_width/src_height: 元動画の解像度
        hp_overlay: shorts.hp_overlay 設定（None なら重畳なし）

    Returns:
        出力ファイルパス
    """
    ffmpeg = _find_ffmpeg()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            ffmpeg,
            "-ss", str(max(0, start)),
            "-i", str(video_path),
            "-t", str(duration),
            *build_filter_args(src_width, src_height, hp_overlay),
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
) -> Path:
    """
    ハイライトイベントから YouTube Shorts 動画を生成する。

    Args:
        video_path: 元の録画動画パス
        events: 検出済みハイライトイベントのリスト
        output_path: 出力先（None なら output/ 以下に自動生成）

    Returns:
        生成した Shorts 動画のパス
    """
    if output_path is None:
        stem = video_path.stem
        output_path = OUTPUT_DIR / f"{stem}_shorts.mp4"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 重複除去 + スコア上位選択（合計 SHORTS_MAX_SEC 以内）→ 時系列順
    selected = select_clips(events)

    if not selected:
        raise ValueError("選択されたハイライトイベントがありません")

    # 各クリップを生成
    clips_dir = OUTPUT_DIR / "clips"
    clips_dir.mkdir(exist_ok=True)
    clip_paths: list[Path] = []

    hp_overlay = hp_overlay_config()

    for i, event in enumerate(selected):
        start = event.timestamp - _effective_pre(event)
        duration = _effective_duration(event)
        clip_out = clips_dir / f"clip_{i:03d}_{event.timestamp:.1f}s.mp4"
        clip_and_crop(video_path, start, duration, clip_out, hp_overlay=hp_overlay)
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
