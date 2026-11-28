"""
ハイライトイベントから YouTube Shorts 用動画を生成する。

処理フロー:
  1. 時間が重なるイベントを場面にまとめ、スコア上位の場面を尺の上限まで選ぶ
  2. 各クリップを 9:16 縦型にクロップ（中央）
  3. 場面を時系列順に結合して Shorts 動画を出力（尺の上限は shorts_budget_sec）
"""

import subprocess
from dataclasses import dataclass
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


# 場面（シーン）単位の選択（2026-11-27）: 時間が重なるイベントを捨てずに1本の連続した
# 場面にまとめる。旧方式は重なる低スコア側を捨てていたため、連射・撃ち合いの2発目
# （撃破のこともある）が映らなかった
SCENE_MERGE_GAP_SEC = 1.0      # 窓の間がこれ未満ならつなげる（1秒未満のジャンプは見づらい）
SCENE_EXTRA_BONUS = 0.1        # 見どころが1つ増えるごとの加点
SCENE_EXTRA_BONUS_MAX = 0.3
SCENE_NOTABLE_SCORE = 0.5      # 加点の対象にするイベントのスコア下限（何も起きない外れは数えない）
SCENE_MIN_TRIMMED_SEC = 3.0    # 削ってまで入れる場面の最短長


@dataclass
class Scene:
    start: float
    end: float
    events: list[HighlightEvent]

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def score(self) -> float:
        """場面内の最高スコア + 他の見どころの数に応じた加点。"""
        if not self.events:
            return 0.0
        scores = sorted((e.score for e in self.events), reverse=True)
        notable = sum(1 for s in scores[1:] if s >= SCENE_NOTABLE_SCORE)
        return round(scores[0] + min(SCENE_EXTRA_BONUS * notable, SCENE_EXTRA_BONUS_MAX), 3)

    @property
    def top(self) -> HighlightEvent:
        return max(self.events, key=lambda e: e.score)


def _window(e: HighlightEvent) -> tuple[float, float]:
    return max(0.0, e.timestamp - _effective_pre(e)), e.timestamp + _effective_post(e)


def _scene_of(events: list[HighlightEvent]) -> Scene:
    ws = [_window(e) for e in events]
    return Scene(min(a for a, _ in ws), max(b for _, b in ws), sorted(events, key=lambda e: e.timestamp))


def build_scenes(events: list[HighlightEvent], gap: float = SCENE_MERGE_GAP_SEC) -> list[Scene]:
    """イベントの窓が重なる（間が gap 未満）ものを1つの場面にまとめる。時系列順。"""
    scenes: list[Scene] = []
    for e in sorted(events, key=lambda e: _window(e)[0]):
        a, b = _window(e)
        if scenes and a < scenes[-1].end + gap:
            last = scenes[-1]
            last.events.append(e)
            last.end = max(last.end, b)
        else:
            scenes.append(Scene(a, b, [e]))
    return scenes


def trim_scene(scene: Scene, max_sec: float) -> Scene:
    """
    max_sec に収まるまで、場面の最初か最後のイベントのうちスコアの低い方を外して縮める
    （同点なら後ろを外す）。イベントが1つになっても収まらなければそのまま返す。
    """
    events = sorted(scene.events, key=lambda e: e.timestamp)
    cur = _scene_of(events)
    while cur.duration > max_sec and len(events) > 1:
        if events[0].score < events[-1].score:
            events = events[1:]
        else:
            events = events[:-1]
        cur = _scene_of(events)
    return cur


def select_scenes(events: list[HighlightEvent], max_total_sec: float = SHORTS_MAX_SEC) -> list[Scene]:
    """
    Shorts に入れる場面を選ぶ。スコアの高い場面から入れ、入りきらない場面は
    残り尺まで削って入れる（SCENE_MIN_TRIMMED_SEC 未満になるなら諦める）。時系列順で返す。
    """
    scenes = build_scenes(events)
    selected: list[Scene] = []
    total = 0.0
    for sc in sorted(scenes, key=lambda s: s.score, reverse=True):
        remaining = max_total_sec - total
        if sc.duration > remaining:
            if remaining < SCENE_MIN_TRIMMED_SEC:
                continue
            sc = trim_scene(sc, remaining)
            if sc.duration > remaining:
                continue
        selected.append(sc)
        total += sc.duration
    if not selected and scenes:
        # 単体でも予算オーバーなら、最高スコアの場面を削って最低1本は残す
        selected.append(trim_scene(max(scenes, key=lambda s: s.score), max_total_sec))
    return sorted(selected, key=lambda s: s.start)


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
    hp_bar: str | None = None,
) -> list[str]:
    """
    クリップ切り出し用の ffmpeg フィルタ引数を組み立てる。

    hp_bar（src.hp_bar.build_hp_bar_filters の出力）があれば、HUD を切り出す代わりに
    描き直した HP バーを重ねる（2026-11-28〜）。

    基本は中央 9:16 クロップ + 拡大。hp_overlay が指定されていれば、
    元フレーム左下 HUD の HP バー矩形（src）を切り出して縦動画上部
    （dst: 幅 w で中央寄せ、上端 y）に重ねる filter_complex になる。
    HP バーは毎フレーム元映像から取るので、被弾によるバーの変化も映る。

    minimap（初弾マップ）が指定されていれば、元フレーム右下のミニマップ矩形
    （src）を dst の位置に、クリップ内の start〜end 秒だけフェード付きで重ねる。
    """
    base = _center_crop_filter(src_width, src_height)
    if hp_bar:
        base = f"{base},{hp_bar}"
        hp_overlay = None  # 描き直したバーを使うので HUD は切り出さない
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
    out_duration: float | None = None,
    blur_fade_sec: float = 0.0,
) -> str:
    """
    早送りつなぎ用のフィルタ。ぼかした戦闘映像を背景に、ミニマップを大写しにして
    早送りマーク（▶▶）を付ける。海外の視聴者も想定し、文字は入れない。

    blur_fade_sec > 0 かつ out_duration があれば、最後の blur_fade_sec 秒でミニマップと
    マークを消し、ぼかしをゆっくり晴らして次の場面へつなぐ（2026-11-28。以前は次の場面で
    一瞬で鮮明に切り替わっていた）。鮮明な映像の上に、ぼかし層を透明度のフェードで重ねる。
    """
    s = minimap_src
    map_h = round(s["h"] * map_width / s["w"])
    map_y = (SHORTS_HEIGHT - map_h) // 2 - 80
    label = "▶▶"  # ▶▶
    crop = _center_crop_filter(src_width, src_height)
    drawtext = (
        f"drawtext=fontfile='{_SYMBOL_FONT}':text='{label}':fontsize=96:fontcolor=white:"
        f"borderw=4:bordercolor=black:x=(w-text_w)/2:y={map_y + map_h + 40}"
    )
    if not (blur_fade_sec > 0 and out_duration and out_duration > blur_fade_sec):
        return (
            f"[0:v]setpts=PTS/{speed},split=2[v][mm];"
            f"[v]{crop},boxblur=20:2,"
            f"eq=brightness=-0.25[bg];"
            f"[mm]crop={s['w']}:{s['h']}:{s['x']}:{s['y']},"
            f"scale={map_width}:{map_h}:flags=lanczos[map];"
            f"[bg][map]overlay=(W-w)/2:{map_y},"
            f"{drawtext}[out]"
        )
    fade_at = round(out_duration - blur_fade_sec, 3)
    shown = f"enable='lt(t,{fade_at})'"
    return (
        f"[0:v]setpts=PTS/{speed},split=3[v][mm][sh];"
        f"[sh]{crop}[sharp];"
        f"[v]{crop},boxblur=20:2,"
        f"eq=brightness=-0.25[bg];"
        f"[mm]crop={s['w']}:{s['h']}:{s['x']}:{s['y']},"
        f"scale={map_width}:{map_h}:flags=lanczos[map];"
        f"[bg][map]overlay=(W-w)/2:{map_y}:{shown},"
        f"{drawtext}:{shown},"
        f"format=yuva420p,fade=t=out:st={fade_at}:d={blur_fade_sec}:alpha=1[blur];"
        f"[sharp][blur]overlay=0:0,format=yuv420p[out]"
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
    blur_fade_sec: float = 0.0,
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
            "-filter_complex", build_travel_filter(src_width, src_height, minimap_src, speed,
                                                   out_duration=out_dur,
                                                   blur_fade_sec=blur_fade_sec),
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
    hp_bar: str | None = None,
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
            *build_filter_args(src_width, src_height, hp_overlay, minimap, hp_bar),
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
    heat=None,
    hp_timeline=None,
) -> Path:
    """
    ハイライトイベントから YouTube Shorts 動画を生成する。

    heat（src.heat.HeatCurve）があれば見どころ曲線のしきい値超えの区間を切り出す
    （2026-11-27〜）。無ければ従来の場面単位の選択（select_scenes）。

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

    # 重なるイベントを場面にまとめ、スコア上位の場面を選ぶ（合計 budget 秒以内）→ 時系列順
    budget = shorts_budget_sec(video_path.stem)
    print(f"  尺の上限: {budget:.0f} 秒")
    from src.minimap import (
        fit_travel_to_budget,
        minimap_config,
        plan_first_shot_overlay,
        plan_travel_segments,
    )
    mm_cfg = minimap_config()
    tr_cfg = (mm_cfg or {}).get("travel") or {}
    use_travel = mm_cfg is not None and tr_cfg.get("enabled", True) and bool(positions)
    # 尺が短いときは早送りも短くする（40秒版で 3 秒の早送りは重すぎる）
    target_sec = min(tr_cfg.get("target_sec", 3.0), max(1.5, budget * 0.04))
    max_out_sec = min(tr_cfg.get("max_out_sec", 5.0), max(2.5, budget * 0.07))

    def choose(clip_budget: float):
        if heat is not None:
            from src.heat import select_segments
            segs, th = select_segments(heat, clip_budget)
            return [Scene(a, b, [e for e in events if a <= e.timestamp <= b]) for a, b in segs], th
        return select_scenes(events, max_total_sec=clip_budget), None

    def plan_travel(scenes):
        if not use_travel:
            return []
        return plan_travel_segments(
            [(sc.start, sc.end) for sc in scenes], positions,
            min_distance_m=tr_cfg.get("min_distance_m", 150.0),
            target_sec=target_sec,
            min_speed=tr_cfg.get("min_speed", 4.0),
            max_speed=tr_cfg.get("max_speed", 16.0),
            max_out_sec=max_out_sec,
        )

    # 移動の早送りは場面より優先して尺を確保する（2026-11-27。40秒版で早送りが全部落ち、
    # 前に出た場面へ唐突に切り替わって位置が分かりにくかった）。場面を選ぶ →
    # その間の早送りの尺を見積もる → 足りなければ場面の予算を減らして選び直す
    clip_budget = budget
    for _ in range(3):
        selected, threshold = choose(clip_budget)
        need = sum(s.out_duration for s in plan_travel(selected))
        if sum(sc.duration for sc in selected) + need <= budget or clip_budget <= budget * 0.5:
            break
        clip_budget = max(budget - need, budget * 0.5)

    if not selected:
        raise ValueError("選択されたハイライトイベントがありません")
    if heat is not None:
        from src.heat import plot_curve
        segs = [(sc.start, sc.end) for sc in selected]
        print(f"  見どころ曲線: しきい値 {threshold:.3f} で {len(segs)} 区間 / "
              f"{sum(b - a for a, b in segs):.1f} 秒（熱の基準 {heat.xp_ref:.0f} XP）")
        try:
            plot_curve(heat, segs, threshold, output_path.with_suffix(".heat.png"))
        except Exception as e:  # 確認用の画像なので失敗しても続行
            print(f"  見どころ曲線の画像を保存できませんでした: {e}")

    # 各クリップを生成
    clips_dir = OUTPUT_DIR / "clips"
    clips_dir.mkdir(exist_ok=True)
    clip_paths: list[Path] = []

    hp_overlay = hp_overlay_config()

    windows = [(sc.start, sc.end) for sc in selected]

    first_shot = None
    travel_after: dict[int, object] = {}
    if mm_cfg is not None:
        fs_cfg = mm_cfg.get("first_shot") or {}
        if fs_cfg.get("enabled", True):
            shot_times = [e.timestamp for e in events if e.event_type.startswith("shot")]
            first_shot = plan_first_shot_overlay(
                windows, shot_times, duration_sec=fs_cfg.get("duration_sec", 3.0)
            )
        if use_travel:
            clips_total = sum(b - a for a, b in windows)
            segs = fit_travel_to_budget(plan_travel(selected), clips_total, budget)
            travel_after = {s.after_clip: s for s in segs}

    for i, scene in enumerate(selected):
        start, duration = scene.start, scene.duration
        clip_out = clips_dir / f"clip_{i:03d}_{scene.start:.1f}s.mp4"
        minimap = None
        if first_shot is not None and first_shot[0] == i:
            minimap = {
                "src": mm_cfg["src"], "dst": mm_cfg["first_shot"]["dst"],
                "start": first_shot[1], "end": first_shot[2],
            }
        hp_bar = None
        if hp_overlay is not None and hp_timeline is not None:
            from src.hp_bar import build_hp_bar_filters
            from src.wot_assets import ensure_hp_assets
            assets = ensure_hp_assets() if hp_overlay.get("style", "game") == "game" else None
            hp_bar = build_hp_bar_filters(hp_timeline, start, duration, hp_overlay["dst"], assets=assets)
        clip_and_crop(video_path, start, duration, clip_out,
                      hp_overlay=hp_overlay, minimap=minimap, hp_bar=hp_bar)
        clip_paths.append(clip_out)
        note = "（初弾マップ）" if minimap else ""
        kinds = ", ".join(e.event_type for e in scene.events) or "-"
        label = (f"heat_max={max(heat.at(scene.start + k * heat.dt) for k in range(int(duration / heat.dt) + 1)):.2f}"
                 if heat is not None else f"score={scene.score:.3f}")
        print(f"  clip {i+1}/{len(selected)}: {scene.start:.1f}-{scene.end:.1f}s "
              f"({duration:.1f}s, {label}, {kinds}) → {clip_out.name}{note}")

        seg = travel_after.get(i)
        if seg is not None:
            travel_out = clips_dir / f"clip_{i:03d}_travel.mp4"
            make_travel_clip(video_path, seg.src_start, seg.src_end, seg.speed,
                             mm_cfg["src"], travel_out,
                             blur_fade_sec=float(tr_cfg.get("blur_fade_sec", 0.4)))
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
