"""
その試合の累計（与ダメ・防いだダメ・アシスト）を画面に出す（2026-11-28）。

ゲーム画面の戦果カウンターのように、動画の中で累計が増えていく。HP バーの下に
ゲームのアイコン（交差した矢印・盾・偵察の電波）と数字を並べる。増えた直後は、ゲームと
同じく数字の後ろに柔らかい光を出して約0.4秒で消す。

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
# 増えたときの光（ゲームの battleDamageLogPanel と同じ。2倍の 252×80 を数字の少し左から置く）
GLOW_STEP_SEC = 0.1        # 不透明度の段階ごとの秒数（4段階で約0.4秒で消える）
GLOW_LEFT = 70             # 光の左端を数字の左端からどれだけ左に出すか（光の中心が数字の少し右に来る位置）
GLOW_H = 80
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
    assist_by: dict[str, int] = {}
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
        if kind == "assist":
            assist_by[e.get("name")] = assist_by.get(e.get("name"), 0) + int(amount)
    if "assist" in out:
        # アシストのアイコンは、その試合で一番多いアシストの種類にする（ゲームも種類ごとのアイコン）
        top = max(assist_by, key=assist_by.get) if assist_by else "RADIO_ASSIST"
        out["_assist_icon"] = {"RADIO_ASSIST": "assist_radio", "TRACK_ASSIST": "assist_track",
                               "STUN_ASSIST": "assist_stun"}.get(top, "assist_radio")
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
                         font: Path, y: int, frame_w: int = 1080, glows: list | None = None) -> str:
    """
    1 クリップ分の累計表示の ffmpeg フィルタ（既存の映像の続きにカンマでつなげる形）。
    時間はクリップ内の秒。icons は src.wot_assets.ensure_battle_icons の戻り値。
    glows（src.wot_assets.ensure_totals_glow: 不透明度の段階ごとの光の PNG）があれば、
    値が増えた直後に数字の後ろへ光を出して GLOW_STEP_SEC ごとに薄くする（ゲームと同じ演出）。
    """
    from src.hp_bar import _ff_path
    kinds = [k for k in KINDS if k in timeline]
    if not kinds:
        return ""
    end = round(clip_dur, 3)
    x0 = (frame_w - ROW_W) // 2
    slot_w = ROW_W // len(KINDS)
    boxes, texts = [], []
    glow_layers: list[tuple[Path, int, int, str]] = []   # (PNG, x, y, enable)
    font_s = _ff_path(font)
    for i, kind in enumerate(kinds):
        sx = x0 + i * slot_w
        num_x = sx + 20 + ICON + 14
        boxes.append(f"drawbox=x={sx + 4}:y={y}:w={slot_w - 8}:h={BOX_H}:color={COLOR_BG}:t=fill")
        changes = timeline[kind]
        bumps = [round(ts - clip_start, 3) for ts, _ in changes if 0 < ts - clip_start < end]
        cuts = [0.0] + bumps + [end]
        for a, b in zip(cuts, cuts[1:]):
            if b <= a:
                continue
            v = _value_at(changes, clip_start + a)
            texts.append(f"drawtext=fontfile='{font_s}':text='{v:,}':fontsize={FONT_SIZE}:"
                         f"fontcolor={TEXT_COLOR}:shadowcolor=black@0.8:shadowx=2:shadowy=2:"
                         f"x={num_x}:y={y}+({BOX_H}-text_h)/2:"
                         f"enable='gte(t,{round(a, 3)})*lt(t,{round(b, 3)})'")
        if glows and bumps:
            for level, png in enumerate(glows):
                wins = "+".join(f"gte(t,{round(c + level * GLOW_STEP_SEC, 3)})*"
                                f"lt(t,{round(c + (level + 1) * GLOW_STEP_SEC, 3)})" for c in bumps)
                glow_layers.append((png, num_x - GLOW_LEFT, y + (BOX_H - GLOW_H) // 2, wins))
    # 重ね順: 枠 → 光 → アイコン → 数字
    parts = [",".join(boxes) + ",null[tt0]"]
    cur, n = "tt0", 0
    layers = [(png, gx, gy, f":enable='{w}'") for png, gx, gy, w in glow_layers]
    def icon_of(kind):
        if kind == "assist":
            return icons.get(timeline.get("_assist_icon", "assist_radio")) or icons.get("assist_radio")
        return icons[kind]
    layers += [(icon_of(k), x0 + i * slot_w + 20, y + (BOX_H - ICON) // 2, "") for i, k in enumerate(kinds)]
    for png, lx, ly, enable in layers:
        parts.append(f"movie='{_ff_path(png)}',format=rgba[ti{n}]")
        parts.append(f"[{cur}][ti{n}]overlay={lx}:{ly}{enable}[tt{n + 1}]")
        cur, n = f"tt{n + 1}", n + 1
    return ";".join(parts) + f";[{cur}]" + ",".join(texts)
