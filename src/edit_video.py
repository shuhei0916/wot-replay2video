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


def shorts_budget_sec(seed: str) -> float:
    """
    この動画の合計尺の上限（秒）。config の shorts.max_total_sec（既定 SHORTS_MAX_SEC）。

    shorts.length_test.enabled なら variants から seed（録画ファイル名）のハッシュで
    1つ選ぶ（A/B テスト。同じ時期に長短を混ぜて比べるため。2026-11-27 導入:
    視聴は尺に関係なく16〜26秒で離脱し、尺が長いほど視聴率が下がっていた）。
    ハッシュなので同じ録画を作り直しても同じ側になる。
    """
    import zlib
    cfg = (load_config().get("shorts") or {})
    test = cfg.get("length_test") or {}
    variants = test.get("variants") or []
    if test.get("enabled") and variants:
        return float(variants[zlib.crc32(seed.encode("utf-8")) % len(variants)])
    return float(cfg.get("max_total_sec", SHORTS_MAX_SEC))
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


def _center_crop_filter(src_width: int, src_height: int) -> str:
    crop_w = int(src_height * 9 / 16)
    crop_x = (src_width - crop_w) // 2
    return (
        f"crop={crop_w}:{src_height}:{crop_x}:0,"
        f"scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}"
    )


def build_filter_args(
    src_width: int,
    src_height: int,
    hp_overlay: dict | None = None,
    minimap: dict | None = None,
) -> list[str]:
    """
    クリップ切り出し用の ffmpeg フィルタ引数を組み立てる。

    基本は中央 9:16 クロップ + 拡大。hp_overlay が指定されていれば、
    元フレーム左下 HUD の HP バー矩形（src）を切り出して縦動画上部
    （dst: 幅 w で中央寄せ、上端 y）に重ねる filter_complex になる。
    HP バーは毎フレーム元映像から取るので、被弾によるバーの変化も映る。

    minimap（初弾マップ）が指定されていれば、元フレーム右下のミニマップ矩形
    （src）を dst の位置に、クリップ内の start〜end 秒だけフェード付きで重ねる。
    """
    base = _center_crop_filter(src_width, src_height)
    if hp_overlay is None and minimap is None:
        return ["-vf", base]

    n = 1 + (hp_overlay is not None) + (minimap is not None)
    parts = [f"[0:v]split={n}[main]" + ("[hud]" if hp_overlay else "") + ("[mm]" if minimap else "")]
    parts.append(f"[main]{base}[bg0]")
    cur = "bg0"

    if hp_overlay is not None:
        s, d = hp_overlay["src"], hp_overlay["dst"]
        dst_w = d["w"]
        dst_h = round(s["h"] * dst_w / s["w"])  # アスペクト比維持
        dst_x = (SHORTS_WIDTH - dst_w) // 2
        parts.append(
            f"[hud]crop={s['w']}:{s['h']}:{s['x']}:{s['y']},"
            f"scale={dst_w}:{dst_h}:flags=lanczos[bar]"
        )
        parts.append(f"[{cur}][bar]overlay={dst_x}:{d['y']}[bg1]")
        cur = "bg1"

    if minimap is not None:
        s, d = minimap["src"], minimap["dst"]
        a, b = minimap["start"], minimap["end"]
        fade = 0.25
        dst_w = d["w"]
        dst_h = round(s["h"] * dst_w / s["w"])
        dst_x = (SHORTS_WIDTH - dst_w) // 2
        parts.append(
            f"[mm]crop={s['w']}:{s['h']}:{s['x']}:{s['y']},"
            f"scale={dst_w}:{dst_h}:flags=lanczos,format=rgba,"
            f"fade=t=in:st={a}:d={fade}:alpha=1,"
            f"fade=t=out:st={max(a, b - fade)}:d={fade}:alpha=1[map]"
        )
        parts.append(
            f"[{cur}][map]overlay={dst_x}:{d['y']}:enable='between(t,{a},{b})'[bg2]"
        )
        cur = "bg2"

    # 最後のラベルを外して出力に流す
    last = parts[-1]
    parts[-1] = last[: last.rfind("[")]
    return ["-filter_complex", ";".join(parts)]


# Shorts の音声は全クリップで揃える（concat -c copy の前提。早送りつなぎの無音と合わせる）
AUDIO_ARGS = ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k"]
VIDEO_ARGS = ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "60"]

# 早送りマーク ▶▶ を確実に描ける記号フォント
_SYMBOL_FONT = "C\\:/Windows/Fonts/seguisym.ttf"


def build_travel_filter(
    src_width: int,
    src_height: int,
    minimap_src: dict,
    speed: float,
    map_width: int = 1000,
) -> str:
    """
    早送りつなぎ用のフィルタ。ぼかした戦闘映像を背景に、ミニマップを大写しにして
    早送りマーク（▶▶）を付ける。海外の視聴者も想定し、文字は入れない。
    """
    s = minimap_src
    map_h = round(s["h"] * map_width / s["w"])
    map_y = (SHORTS_HEIGHT - map_h) // 2 - 80
    label = "▶▶"  # ▶▶
    return (
        f"[0:v]setpts=PTS/{speed},split=2[v][mm];"
        f"[v]{_center_crop_filter(src_width, src_height)},boxblur=20:2,"
        f"eq=brightness=-0.25[bg];"
        f"[mm]crop={s['w']}:{s['h']}:{s['x']}:{s['y']},"
        f"scale={map_width}:{map_h}:flags=lanczos[map];"
        f"[bg][map]overlay=(W-w)/2:{map_y},"
        f"drawtext=fontfile='{_SYMBOL_FONT}':text='{label}':fontsize=96:fontcolor=white:"
        f"borderw=4:bordercolor=black:x=(w-text_w)/2:y={map_y + map_h + 40}[out]"
    )


def make_travel_clip(
    video_path: Path,
    src_start: float,
    src_end: float,
    speed: float,
    minimap_src: dict,
    output_path: Path,
    src_width: int = 1920,
    src_height: int = 1080,
) -> Path:
    """移動区間を早送りし、ミニマップ大写しのつなぎクリップ（無音）を作る。"""
    ffmpeg = _find_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_dur = (src_end - src_start) / speed
    subprocess.run(
        [
            ffmpeg,
            "-ss", str(max(0, src_start)),
            "-t", str(src_end - src_start),
            "-i", str(video_path),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", build_travel_filter(src_width, src_height, minimap_src, speed),
            "-map", "[out]", "-map", "1:a",
            "-t", f"{out_dur:.3f}",
            *VIDEO_ARGS,
            *AUDIO_ARGS,
            "-y",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )
    return output_path


def clip_and_crop(
    video_path: Path,
    start: float,
    duration: float,
    output_path: Path,
    src_width: int = 1920,
    src_height: int = 1080,
    hp_overlay: dict | None = None,
    minimap: dict | None = None,
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
            *build_filter_args(src_width, src_height, hp_overlay, minimap),
            *VIDEO_ARGS,
            *AUDIO_ARGS,
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
    positions: list[tuple[float, float, float]] | None = None,
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

    # 重複除去 + スコア上位選択（合計 budget 秒以内）→ 時系列順
    budget = shorts_budget_sec(video_path.stem)
    print(f"  尺の上限: {budget:.0f} 秒")
    selected = select_clips(events, max_total_sec=budget)

    if not selected:
        raise ValueError("選択されたハイライトイベントがありません")

    # 各クリップを生成
    clips_dir = OUTPUT_DIR / "clips"
    clips_dir.mkdir(exist_ok=True)
    clip_paths: list[Path] = []

    hp_overlay = hp_overlay_config()

    from src.minimap import (
        fit_travel_to_budget,
        minimap_config,
        plan_first_shot_overlay,
        plan_travel_segments,
    )
    mm_cfg = minimap_config()
    windows = [
        (e.timestamp - _effective_pre(e), e.timestamp + _effective_post(e)) for e in selected
    ]

    first_shot = None
    travel_after: dict[int, object] = {}
    if mm_cfg is not None:
        fs_cfg = mm_cfg.get("first_shot") or {}
        if fs_cfg.get("enabled", True):
            shot_times = [e.timestamp for e in events if e.event_type.startswith("shot")]
            first_shot = plan_first_shot_overlay(
                windows, shot_times, duration_sec=fs_cfg.get("duration_sec", 3.0)
            )
        tr_cfg = mm_cfg.get("travel") or {}
        if tr_cfg.get("enabled", True) and positions:
            segs = plan_travel_segments(
                windows, positions,
                min_distance_m=tr_cfg.get("min_distance_m", 150.0),
                target_sec=tr_cfg.get("target_sec", 3.0),
                min_speed=tr_cfg.get("min_speed", 4.0),
                max_speed=tr_cfg.get("max_speed", 16.0),
                max_out_sec=tr_cfg.get("max_out_sec", 5.0),
            )
            clips_total = sum(b - a for a, b in windows)
            segs = fit_travel_to_budget(segs, clips_total, budget)
            travel_after = {s.after_clip: s for s in segs}

    for i, event in enumerate(selected):
        start = event.timestamp - _effective_pre(event)
        duration = _effective_duration(event)
        clip_out = clips_dir / f"clip_{i:03d}_{event.timestamp:.1f}s.mp4"
        minimap = None
        if first_shot is not None and first_shot[0] == i:
            minimap = {
                "src": mm_cfg["src"], "dst": mm_cfg["first_shot"]["dst"],
                "start": first_shot[1], "end": first_shot[2],
            }
        clip_and_crop(video_path, start, duration, clip_out,
                      hp_overlay=hp_overlay, minimap=minimap)
        clip_paths.append(clip_out)
        note = "（初弾マップ）" if minimap else ""
        print(f"  clip {i+1}/{len(selected)}: {event.timestamp:.1f}s (score={event.score:.3f}) → {clip_out.name}{note}")

        seg = travel_after.get(i)
        if seg is not None:
            travel_out = clips_dir / f"clip_{i:03d}_travel.mp4"
            make_travel_clip(video_path, seg.src_start, seg.src_end, seg.speed,
                             mm_cfg["src"], travel_out)
            clip_paths.append(travel_out)
            print(f"    早送り {seg.src_start:.0f}-{seg.src_end:.0f}s "
                  f"({seg.distance_m:.0f}m, ×{seg.speed:g}, {seg.out_duration:.1f}s)")

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
