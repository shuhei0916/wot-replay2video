"""
自車の HP バーを動画用に描き直す（2026-11-28）。

以前は HUD の HP バー（224×18 px）を切り出して 874 px に拡大していたため、そこだけ
粗かった。mod が記録した被弾（v8 は被弾後の HP と最大 HP、v7 はダメージ量）と
戦闘結果の最大 HP から HP の推移を作り、ffmpeg の drawbox / drawtext でくっきり描く。

- 残り HP の割合で色が連続的に変わる（WoT の HUD から測った色のグラデーション）
- 削られた分は明るい色で残り、0.25秒で縮む（ゲームの HP バー風）
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
COLOR_BG = "black@0.85"     # ゲームの HP バーの空き部分（ほぼ不透明の黒）に合わせる
# 残り HP の割合 → 色（WoT の HUD と同じグラデーション）。2026-11-28 に録画内のゲームの
# HP バー（A-44, 最大1200）を残り HP ごとに切り出し、塗り部分の中央値 RGB を測った値。
# 間は線形補間する（ゲームも緑 → 黄緑 → 黄 → 橙 → 赤 と連続的に変わる）
HP_COLOR_STOPS = [
    (1.000, (5, 89, 4)),
    (0.747, (82, 88, 6)),
    (0.674, (102, 88, 9)),
    (0.603, (124, 87, 6)),
    (0.378, (170, 69, 2)),
    (0.158, (169, 29, 1)),
    (0.000, (169, 29, 1)),
]
COLOR_DRAIN = "0xfff2d6@0.9"  # 削られた分（遅れて減る）
DRAIN_DELAY_SEC = 0.0       # 被弾から減り始めるまで
DRAIN_SEC = 0.25            # 減りきるまで（2026-11-28: 0.25秒後から0.5秒 → すぐに0.25秒へ）
DRAIN_STEPS = 5             # drawbox は時間で幅を変えられないので段階的に描く


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


# 網目模様がバー中央を平均どれだけ暗くするか（hp_mesh.png の中央部の平均不透明度）。
# HP_COLOR_STOPS は網目込みの見た目を測った値なので、網目を重ねるときは塗りをこの分だけ
# 明るくして、重ねた結果がゲームの見た目と同じになるようにする
MESH_CENTER_ALPHA = 0.358


def hp_color(ratio: float, under_mesh: bool = False) -> str:
    """
    残り HP の割合（0〜1）の色を ffmpeg の色指定（0xRRGGBB）で返す。
    under_mesh=True なら網目模様を重ねる前提の塗り色（MESH_CENTER_ALPHA の分だけ明るい）。
    """
    ratio = max(0.0, min(1.0, ratio))
    rgb = list(HP_COLOR_STOPS[-1][1])
    for (r_hi, c_hi), (r_lo, c_lo) in zip(HP_COLOR_STOPS, HP_COLOR_STOPS[1:]):
        if r_lo <= ratio <= r_hi:
            k = 0.0 if r_hi == r_lo else (ratio - r_lo) / (r_hi - r_lo)
            rgb = [lo + (hi - lo) * k for lo, hi in zip(c_lo, c_hi)]
            break
    if under_mesh:
        rgb = [v / (1 - MESH_CENTER_ALPHA) for v in rgb]
    return "0x" + "".join(f"{min(255, round(v)):02x}" for v in rgb)


def _fmt(v: float) -> str:
    return f"{int(round(v)):,}"


def _ff_path(path: Path) -> str:
    """ffmpeg のフィルタ引数に入れるパス（C: のコロンをエスケープ）。"""
    return str(Path(path).resolve()).replace("\\", "/").replace(":", "\\:")


# ゲームの部品で組み立てるときの配置（ゲームの画素単位。src.wot_assets.HP_SCALE 倍して使う）
# 枠画像（227×19）の中で、網目（220×14）は (3, 2) に置く。網目の一番上の行は完全に透明なので、
# 塗りはその1行下から13行だけにする（塗ると網目のかからない明るい線が1本出てしまうため）
GAME_MESH_XY = (3, 2)
GAME_FILL_Y = 3
GAME_FILL_H = 13
GAME_FILL_W = 220
GAME_FONT_SIZE = 11.5        # ゲームの画素単位（4倍で46）
GAME_TEXT_COLOR = "0xc4c4bc"  # ゲームの数字の灰色（録画では圧縮でやや暗く見える）


def _segments(timeline: HpTimeline, clip_start: float, end: float):
    """クリップ内で HP が一定の区間 [(開始, 終了, HP)] と、被弾 [(時刻, 前の HP, 後の HP)]。"""
    segs: list[tuple[float, float, float]] = []
    drains: list[tuple[float, float, float]] = []
    cur_t, cur_hp = 0.0, timeline.hp_at(clip_start)
    for ts, v in timeline.changes:
        lt = round(ts - clip_start, 3)
        if lt <= 0 or lt >= end:
            continue
        if v != cur_hp:
            segs.append((cur_t, lt, cur_hp))
            drains.append((lt, cur_hp, v))
            cur_t, cur_hp = lt, v
    segs.append((cur_t, end, cur_hp))
    return segs, drains


def _drain_boxes(drains, width, x, y, h, end) -> list[str]:
    """削られた分（明るい色）: 被弾直後は前の HP の幅で残し、段階的に縮める。"""
    out = []
    for c, before, after in drains:
        wb, wa = width(before), width(after)
        if wb <= wa:
            continue
        if DRAIN_DELAY_SEC > 0:
            out.append(f"drawbox=x={x + wa}:y={y}:w={wb - wa}:h={h}:color={COLOR_DRAIN}:t=fill:"
                       f"enable='gte(t,{c})*lt(t,{min(c + DRAIN_DELAY_SEC, end)})'")
        step = DRAIN_SEC / DRAIN_STEPS
        for k in range(DRAIN_STEPS):
            a = round(c + DRAIN_DELAY_SEC + k * step, 3)
            b = round(a + step, 3)
            if a >= end:
                break
            dw = round((wb - wa) * (1 - k / DRAIN_STEPS))
            if dw > 0:
                out.append(f"drawbox=x={x + wa}:y={y}:w={dw}:h={h}:color={COLOR_DRAIN}:t=fill:"
                           f"enable='gte(t,{a})*lt(t,{min(b, end)})'")
    return out


def build_hp_bar_filters(timeline: HpTimeline, clip_start: float, clip_dur: float,
                         dst: dict, frame_w: int = 1080, assets: dict | None = None) -> str:
    """
    1 クリップ分の HP バーを描く ffmpeg フィルタ。時間はクリップ内の秒（0〜clip_dur）。
    dst は shorts.hp_overlay.dst（y: 上端。簡易描画では w: 幅も使う）。

    assets（src.wot_assets.ensure_hp_assets）があれば、ゲームと同じ部品（枠・網目・フォント）
    で組み立てる: 枠を置く → 塗り → 網目を重ねる → 数字。無ければ図形だけの簡易描画。
    出力は「既存の映像の続きにカンマでつなげる」形（先頭はフィルタ）。
    """
    end = round(clip_dur, 3)
    m = timeline.max_hp
    segs, drains = _segments(timeline, clip_start, end)

    if assets:
        k = int(assets["scale"])
        frame_wpx, frame_hpx = 227 * k, 19 * k
        X = (frame_w - frame_wpx) // 2
        Y = int(dst["y"])
        fill_x, fill_y = X + GAME_MESH_XY[0] * k, Y + GAME_FILL_Y * k
        fill_w, fill_h = GAME_FILL_W * k, GAME_FILL_H * k

        def width(hp: float) -> int:
            return max(0, min(fill_w, round(fill_w * hp / m)))

        boxes = _drain_boxes(drains, width, fill_x, fill_y, fill_h, end)
        texts = []
        font = _ff_path(assets["font"])
        for a, b, hp in segs:
            win = f"enable='gte(t,{round(a, 3)})*lt(t,{round(b, 3)})'"
            wh = width(hp)
            if wh > 0:
                boxes.append(f"drawbox=x={fill_x}:y={fill_y}:w={wh}:h={fill_h}:"
                             f"color={hp_color(hp / m, under_mesh=True)}:t=fill:{win}")
            texts.append(f"drawtext=fontfile='{font}':text='{int(round(hp))}/{int(round(m))}':"
                         f"fontsize={round(GAME_FONT_SIZE * k)}:fontcolor={GAME_TEXT_COLOR}:"
                         f"shadowcolor=black@0.7:shadowx={k // 2}:shadowy={k // 2}:"
                         f"x={X}+({frame_wpx}-text_w)/2:y={Y}+({frame_hpx}-text_h)/2:{win}")
        return (f"null[hp0];movie='{_ff_path(assets['frame'])}'[hpf];[hp0][hpf]overlay={X}:{Y}"
                + "".join("," + b for b in boxes)
                + f"[hp1];movie='{_ff_path(assets['mesh'])}',format=rgba[hpm];"
                f"[hp1][hpm]overlay={X + GAME_MESH_XY[0] * k}:{Y + GAME_MESH_XY[1] * k},"
                + ",".join(texts))

    # 簡易描画（部品が取れないとき）
    w = int(dst["w"])
    x0 = (frame_w - w) // 2
    y0 = int(dst["y"])
    inner_x, inner_y = x0 + BORDER, y0 + BORDER
    inner_w, inner_h = w - 2 * BORDER, BAR_H - 2 * BORDER

    def width(hp: float) -> int:
        return max(0, min(inner_w, round(inner_w * hp / m)))

    f = [f"drawbox=x={x0}:y={y0}:w={w}:h={BAR_H}:color={COLOR_BG}:t=fill"]
    f += _drain_boxes(drains, width, inner_x, inner_y, inner_h, end)
    for a, b, hp in segs:
        win = f"enable='gte(t,{round(a, 3)})*lt(t,{round(b, 3)})'"
        wh = width(hp)
        if wh > 0:
            f.append(f"drawbox=x={inner_x}:y={inner_y}:w={wh}:h={inner_h}:"
                     f"color={hp_color(hp / m)}:t=fill:{win}")
        f.append(f"drawtext=fontfile='{FONT}':text='{_fmt(hp)} / {_fmt(m)}':fontsize={FONT_SIZE}:"
                 f"fontcolor=white:borderw=3:bordercolor=black@0.8:"
                 f"x={x0}+({w}-text_w)/2:y={y0}+({BAR_H}-text_h)/2:{win}")
    return ",".join(f)
