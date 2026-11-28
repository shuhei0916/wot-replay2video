"""
その試合の累計（与ダメ・防いだダメ・アシスト）を画面に出す（2026-11-28）。

ゲーム画面の戦果カウンターのように、動画の中で累計が増えていく。HP バーの下に
ゲームのアイコン（交差した矢印・盾・偵察の電波）と数字を並べる。増えた直後は数字を
少しの間だけ金色にして、増えたことが分かるようにする。

データは mod v10 の戦果イベント（battle_event: DAMAGE / TANKING / RADIO・TRACK・STUN_ASSIST）。
v10 より前の記録は damage_dealt から与ダメだけを出す。
"""

import json
from pathlib import Path

KINDS = ("damage", "blocked", "assist")
_EVENT_KIND = {"DAMAGE": "damage", "TANKING": "blocked",
               "RADIO_ASSIST": "assist", "TRACK_ASSIST": "assist", "STUN_ASSIST": "assist"}

# 見た目（出力 1080×1920 上。HP バー 908 px と同じ幅に3枠）
ROW_W = 908
BOX_H = 72
ICON = 56
FONT_SIZE = 50
TEXT_COLOR = "0xe8e8e0"
FLASH_COLOR = "0xffc94a"   # 増えた直後
FLASH_SEC = 0.5
COLOR_BG = "black@0.6"


def totals_timeline(data: dict, rec_start: float) -> dict[str, list[tuple[float, int]]]:
    """
    {種類: [(録画内の秒, その時点の累計), ...]}。記録が無い種類は入れない
    （v10 の戦果イベントがあれば3種類、無ければ damage_dealt から与ダメだけ）。
    """
    events = sorted(data.get("events", []), key=lambda e: e.get("epoch", 0))
    has_battle_events = any(e.get("type") == "battle_event" for e in events)
    out: dict[str, list[tuple[float, int]]] = {k: [] for k in (KINDS if has_battle_events else ("damage",))}
    acc = {k: 0 for k in out}
    for e in events:
        if has_battle_events:
            if e.get("type") != "battle_event":
                continue
            kind = _EVENT_KIND.get(e.get("name"))
            d = e.get("details")
            amount = d[0] if isinstance(d, list) and d else 0
        else:
            if e.get("type") != "damage_dealt":
                continue
            kind, amount = "damage", e.get("damage") or 0
        if kind is None or not amount:
            continue
        try:
            ts = round(float(e["epoch"]) - rec_start, 2)
        except (KeyError, TypeError, ValueError):
            continue
        acc[kind] += int(amount)
        out[kind].append((ts, acc[kind]))
    return out


def load_totals(recording_path: Path) -> dict | None:
    recording_path = Path(recording_path)
    try:
        meta = json.loads(recording_path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        data = json.loads(recording_path.with_suffix(".events.json").read_text(encoding="utf-8"))
        return totals_timeline(data, float(meta["rec_start_epoch"]))
    except (OSError, ValueError, KeyError):
        return None


def _value_at(changes: list[tuple[float, int]], t: float) -> int:
    v = 0
    for ts, total in changes:
        if ts > t:
            break
        v = total
    return v


def build_totals_filters(timeline: dict, clip_start: float, clip_dur: float, icons: dict,
                         font: Path, y: int, frame_w: int = 1080) -> str:
    """
    1 クリップ分の累計表示の ffmpeg フィルタ（既存の映像の続きにカンマでつなげる形）。
    時間はクリップ内の秒。icons は src.wot_assets.ensure_battle_icons の戻り値。
    """
    from src.hp_bar import _ff_path
    kinds = [k for k in KINDS if k in timeline]
    if not kinds:
        return ""
    end = round(clip_dur, 3)
    x0 = (frame_w - ROW_W) // 2
    slot_w = ROW_W // len(KINDS)
    boxes, texts = [], []
    font_s = _ff_path(font)
    for i, kind in enumerate(kinds):
        sx = x0 + i * slot_w
        boxes.append(f"drawbox=x={sx + 4}:y={y}:w={slot_w - 8}:h={BOX_H}:color={COLOR_BG}:t=fill")
        changes = timeline[kind]
        # クリップ内で値が一定の区間と、増えた直後（金色）の区間
        cuts = [0.0] + [round(ts - clip_start, 3) for ts, _ in changes if 0 < ts - clip_start < end] + [end]
        for a, b in zip(cuts, cuts[1:]):
            if b <= a:
                continue
            v = _value_at(changes, clip_start + a)
            flash_end = min(b, a + FLASH_SEC) if a > 0 else a
            spans = [(a, flash_end, FLASH_COLOR), (flash_end, b, TEXT_COLOR)] if flash_end > a else [(a, b, TEXT_COLOR)]
            for s0, s1, color in spans:
                if s1 <= s0:
                    continue
                texts.append(f"drawtext=fontfile='{font_s}':text='{v:,}':fontsize={FONT_SIZE}:"
                             f"fontcolor={color}:shadowcolor=black@0.8:shadowx=2:shadowy=2:"
                             f"x={sx + 20 + ICON + 14}:y={y}+({BOX_H}-text_h)/2:"
                             f"enable='gte(t,{round(s0, 3)})*lt(t,{round(s1, 3)})'")
    parts = [",".join(boxes) + ",null[tt0]"]
    cur = "tt0"
    for i, kind in enumerate(kinds):
        sx = x0 + i * slot_w
        parts.append(f"movie='{_ff_path(icons[kind])}',format=rgba[ti{i}]")
        parts.append(f"[{cur}][ti{i}]overlay={sx + 20}:{y + (BOX_H - ICON) // 2}[tt{i + 1}]")
        cur = f"tt{i + 1}"
    return ";".join(parts) + f";[{cur}]" + ",".join(texts)
