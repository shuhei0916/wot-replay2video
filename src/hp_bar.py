"""
自車の HP バーを動画用に描き直す（2026-11-28）。

以前は HUD の HP バー（224×18 px）を切り出して 874 px に拡大していたため、そこだけ
粗かった。mod が記録した被弾（v8 は被弾後の HP と最大 HP、v7 はダメージ量）と
戦闘結果の最大 HP から HP の推移を作り、ffmpeg の drawbox / drawtext でくっきり描く。

- 残り HP の割合で色が変わる（緑 → 黄 → 赤）
- 削られた分は明るい色で残り、少し遅れて減る（ゲームの HP バー風）
- 「975 / 1,480」の数値を出す
- HUD のスケールを変えても座標を測り直す必要がない

推移が作れない録画（mod の記録が無い）は従来どおり HUD を切り出す。
"""

import json
from dataclasses import dataclass
from pathlib import Path

# 見た目（出力 1080×1920 上の座標。x は中央寄せ）
BAR_H = 56
BORDER = 4
FONT = "C\\:/Windows/Fonts/bahnschrift.ttf"   # 数字が読みやすい等幅寄りのフォント
FONT_SIZE = 40
COLOR_BG = "black@0.55"
COLOR_HIGH = "0x3fd16b"     # 50% 超
COLOR_MID = "0xf2c230"      # 25〜50%
COLOR_LOW = "0xe5484d"      # 25% 未満
COLOR_DRAIN = "0xfff2d6@0.9"  # 削られた分（遅れて減る）
DRAIN_DELAY_SEC = 0.25      # 被弾から減り始めるまで
DRAIN_SEC = 0.5             # 減りきるまで
DRAIN_STEPS = 6             # drawbox は時間で幅を変えられないので段階的に描く


@dataclass
class HpTimeline:
    max_hp: float
    changes: list[tuple[float, float]]  # (録画内の秒, その時点からの HP)。時系列順

    def hp_at(self, t: float) -> float:
        hp = self.max_hp
        for ts, v in self.changes:
            if ts > t:
                break
            hp = v
        return hp


def load_hp_timeline(recording_path: Path, max_hp: float | None) -> HpTimeline | None:
    """
    録画のサイドカーから HP の推移を作る。v8 は hit_taken の health / max_health を
    そのまま使い、v7 は最大 HP（max_hp: 戦闘結果の maxHealth）からダメージを引いていく。
    最大 HP が分からない・記録が無い場合は None。
    """
    recording_path = Path(recording_path)
    try:
        meta = json.loads(recording_path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        data = json.loads(recording_path.with_suffix(".events.json").read_text(encoding="utf-8"))
        rec_start = float(meta["rec_start_epoch"])
    except (OSError, ValueError, KeyError):
        return None
    return hp_timeline_from_events(data, rec_start, max_hp)


def hp_timeline_from_events(data: dict, rec_start: float, max_hp: float | None) -> HpTimeline | None:
    hits = sorted((e for e in data.get("events", []) if e.get("type") in ("hit_taken", "death")),
                  key=lambda e: e.get("epoch", 0))
    for e in hits:
        if e.get("max_health"):
            max_hp = float(e["max_health"])
            break
    if not max_hp:
        # v7 の記録: damage_pct = damage / 最大 HP なので逆算できる
        for e in hits:
            if e.get("damage") and e.get("damage_pct"):
                max_hp = round(float(e["damage"]) / float(e["damage_pct"]))
                break
    if not max_hp:
        return None
    hp = float(max_hp)
    changes: list[tuple[float, float]] = []
    for e in hits:
        try:
            ts = round(float(e["epoch"]) - rec_start, 2)
        except (KeyError, TypeError, ValueError):
            continue
        if e.get("type") == "death":
            hp = 0.0
        elif e.get("health") is not None:
            hp = max(float(e["health"]), 0.0)
        else:
            hp = max(hp - float(e.get("damage") or 0), 0.0)
        changes.append((ts, hp))
    return HpTimeline(float(max_hp), changes)


def _color(ratio: float) -> str:
    if ratio > 0.5:
        return COLOR_HIGH
    if ratio >= 0.25:
        return COLOR_MID
    return COLOR_LOW


def _fmt(v: float) -> str:
    return f"{int(round(v)):,}"


def build_hp_bar_filters(timeline: HpTimeline, clip_start: float, clip_dur: float,
                         dst: dict, frame_w: int = 1080) -> str:
    """
    1 クリップ分の HP バーを描く ffmpeg フィルタ（drawbox / drawtext をカンマでつないだもの）。
    時間はクリップ内の秒（0〜clip_dur）。dst は shorts.hp_overlay.dst（w: 幅, y: 上端）。
    """
    w = int(dst["w"])
    x0 = (frame_w - w) // 2
    y0 = int(dst["y"])
    inner_x, inner_y = x0 + BORDER, y0 + BORDER
    inner_w, inner_h = w - 2 * BORDER, BAR_H - 2 * BORDER
    m = timeline.max_hp
    end = round(clip_dur, 3)

    def width(hp: float) -> int:
        return max(0, min(inner_w, round(inner_w * hp / m)))

    # クリップ内で HP が一定の区間
    segs: list[tuple[float, float, float]] = []   # (開始, 終了, HP)
    cur_t, cur_hp = 0.0, timeline.hp_at(clip_start)
    drains: list[tuple[float, float, float]] = []  # (被弾の時刻, 前の HP, 後の HP)
    for ts, v in timeline.changes:
        lt = round(ts - clip_start, 3)
        if lt <= 0 or lt >= end:
            continue
        if v != cur_hp:
            segs.append((cur_t, lt, cur_hp))
            drains.append((lt, cur_hp, v))
            cur_t, cur_hp = lt, v
    segs.append((cur_t, end, cur_hp))

    f = [f"drawbox=x={x0}:y={y0}:w={w}:h={BAR_H}:color={COLOR_BG}:t=fill"]
    # 削られた分（明るい色）: 被弾直後は前の HP の幅で残し、DRAIN_DELAY_SEC 後から段階的に縮める
    for c, before, after in drains:
        wb, wa = width(before), width(after)
        if wb <= wa:
            continue
        hold_end = min(c + DRAIN_DELAY_SEC, end)
        f.append(f"drawbox=x={inner_x + wa}:y={inner_y}:w={wb - wa}:h={inner_h}:"
                 f"color={COLOR_DRAIN}:t=fill:enable='between(t,{c},{hold_end})'")
        step = DRAIN_SEC / DRAIN_STEPS
        for k in range(1, DRAIN_STEPS):
            a = round(c + DRAIN_DELAY_SEC + (k - 1) * step, 3)
            b = round(a + step, 3)
            if a >= end:
                break
            dw = round((wb - wa) * (1 - k / DRAIN_STEPS))
            if dw > 0:
                f.append(f"drawbox=x={inner_x + wa}:y={inner_y}:w={dw}:h={inner_h}:"
                         f"color={COLOR_DRAIN}:t=fill:enable='between(t,{a},{min(b, end)})'")
    # 残り HP と数値
    for a, b, hp in segs:
        win = f"enable='between(t,{round(a, 3)},{round(b, 3)})'"
        wh = width(hp)
        if wh > 0:
            f.append(f"drawbox=x={inner_x}:y={inner_y}:w={wh}:h={inner_h}:"
                     f"color={_color(hp / m)}:t=fill:{win}")
        f.append(f"drawtext=fontfile='{FONT}':text='{_fmt(hp)} / {_fmt(m)}':fontsize={FONT_SIZE}:"
                 f"fontcolor=white:borderw=3:bordercolor=black@0.8:"
                 f"x={x0}+({w}-text_w)/2:y={y0}+({BAR_H}-text_h)/2:{win}")
    return ",".join(f)
