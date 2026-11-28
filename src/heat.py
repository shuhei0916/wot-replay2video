"""
見どころ曲線（ヒート制）: 横軸=録画の時間、縦軸=その瞬間がどれだけ「アツいか」。

出来事（与ダメ・命中・撃破・被弾）があると熱が上がり、何もなければ指数的に冷めていく。
撃ち合いが続くと冷めきる前に熱が積み上がり、長い山になる。熱がしきい値を超えている
区間（+ 頭に照準の溜め）を Shorts に切り出す。場面の選択・長さ・まとめが1本の曲線で決まる。

熱の量は WoT の経験値の推定値を土台にする（有志の線形回帰、15万リプレイ、R²=0.88:
reddit r/WorldofTanks 92c3c4 "Approximate XP calculation values"）。経験値に出ない
映像としての見どころ（撃破の決定的瞬間・非貫通・被弾）は DRAMA_* で足す。

車両の Tier による差（高 Tier ほどダメージが大きく熱が高くなる）は、その車両の平均的な
1戦の戦闘経験値（WN8 期待値表の expDamage / expFrag / expSpot から換算）で割って揃える。
つまり熱 1.0 = 「この車両の平均的な1戦ぶんの戦闘経験値」。

前提: mod v7 の hit_dealt / damage_dealt がある録画。無ければ None を返し、
呼び出し側は従来の場面単位の選択（edit_video.select_scenes）を使う。
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# 経験値の推定値（1あたり）
XP_PER_DAMAGE = 0.1
XP_PER_DAMAGING_HIT = 10.0
XP_PER_KILL = 54.0
XP_PER_SPOT = 7.2

# 経験値に出ない、映像としての見どころ（経験値換算）
DRAMA_KILL = 0.0               # 撃破の上乗せ（2026-11-27: 経験値どおりにする。撃破は経験値54だけで十分目立つ）
DRAMA_NO_DAMAGE_HIT = 5.0      # 非貫通・跳弾（当たったが抜けない「溜め」）
DRAMA_PER_DAMAGE_TAKEN = 0.05  # 被弾（受けたダメージ1あたり。与ダメの半分）

# 曲線と切り出し
DT = 0.1                 # 曲線の刻み（秒）
HALF_LIFE_SEC = 5.0      # 熱が半分に冷めるまでの秒数
THRESHOLD = 0.1          # 入るしきい値: 熱がこれを超えた山を見どころとみなす（熱 = 平均的な
                         # 1戦の戦闘経験値に対する割合。T29 では普通の命中1発で約0.2）
EXIT_RATIO = 0.6         # 出るしきい値 = 入るしきい値 × これ。区間は熱がここを下回るまで続く
                         # （0.5 だと T29 の近い撃破2つが間の命中とつながり28秒の1区間になって40秒版から落ちた）
                         # （ヒステリシス。入るぎりぎりの山でも約3.7秒は熱が残り、細切れにならない）
MIN_THRESHOLD = 0.03     # 総尺が下限に届かないとき、ここまでしきい値を下げてよい
LEAD_SEC = 2.5           # 区間の頭に足す照準の溜め（熱は出来事の瞬間に跳ね上がるため）
TAIL_SEC = 0.0           # 区間の終わりに足す余韻。射撃後の長さは熱の冷め方と出るしきい値で決まるので
                         # 足さない（2026-11-28。役割が重なっていた）
MERGE_GAP_SEC = 1.0      # 区間の間がこれ未満ならつなぐ
MIN_TOTAL_SEC = 15.0     # 総尺の下限（これ未満ならしきい値を下げて広げる）
FALLBACK_XP_REF = 200.0  # 期待値表に無い車両の基準


@dataclass
class HeatInput:
    timestamp: float  # 録画内の秒
    xp: float         # 経験値換算の熱の量
    kind: str


@dataclass
class HeatCurve:
    values: np.ndarray  # 熱（基準で割った値）。index * dt 秒
    dt: float
    xp_ref: float
    inputs: list[HeatInput]

    def at(self, t: float) -> float:
        i = int(round(t / self.dt))
        return float(self.values[i]) if 0 <= i < len(self.values) else 0.0


# ---------------------------------------------------------------------------
# 入力: mod の記録 → 熱の出来事
# ---------------------------------------------------------------------------

def heat_inputs(data: dict, rec_start_epoch: float) -> list[HeatInput] | None:
    """mod の記録から熱の出来事を作る。v7 の与ダメ・命中記録が無ければ None。"""
    events = data.get("events", [])
    if not any(e.get("type") in ("hit_dealt", "damage_dealt") for e in events):
        return None
    out: list[HeatInput] = []
    for e in events:
        et = e.get("type")
        try:
            ts = round(float(e["epoch"]) - rec_start_epoch, 2)
        except (KeyError, TypeError, ValueError):
            continue
        if ts < 0:
            continue
        if et == "damage_dealt":
            dmg = float(e.get("damage") or 0)
            if dmg > 0:
                out.append(HeatInput(ts, XP_PER_DAMAGE * dmg, "damage"))
            if e.get("killed"):
                out.append(HeatInput(ts, XP_PER_KILL + DRAMA_KILL, "kill"))
        elif et == "hit_dealt":
            if e.get("damage_factor"):
                out.append(HeatInput(ts, XP_PER_DAMAGING_HIT, "hit"))
            else:
                out.append(HeatInput(ts, DRAMA_NO_DAMAGE_HIT, "no_damage_hit"))
        elif et == "hit_taken":
            dmg = float(e.get("damage") or 0)
            if dmg > 0:
                out.append(HeatInput(ts, DRAMA_PER_DAMAGE_TAKEN * dmg, "taken"))
    out += _blind_kill_inputs(data, rec_start_epoch)
    return sorted(out, key=lambda h: h.timestamp)


def _blind_kill_inputs(data: dict, rec_start_epoch: float) -> list[HeatInput]:
    """
    視認外の敵の撃破（src.detect_mod_events.blind_kills）の熱。ダメージ量は届いていないので、
    その試合で自車が射撃で与えた1発あたりの平均ダメージで代用する（無ければ撃破の経験値だけ）。
    時刻はキルログの時刻（撃破の瞬間）。
    """
    from src.detect_mod_events import blind_kills
    kills = blind_kills(data)
    if not kills:
        return []
    dealt = [float(e.get("damage") or 0) for e in data.get("events", [])
             if e.get("type") == "damage_dealt" and e.get("reason", 0) in (0, None)]
    avg = sum(dealt) / len(dealt) if dealt else 0.0
    out = []
    for e in kills:
        try:
            ts = round(float(e["epoch"]) - rec_start_epoch, 2)
        except (KeyError, TypeError, ValueError):
            continue
        if ts >= 0:
            out.append(HeatInput(ts, XP_PER_DAMAGE * avg + XP_PER_KILL + DRAMA_KILL, "blind_kill"))
    return out


def xp_reference(type_comp_descr: int | None) -> float:
    """
    その車両の平均的な1戦の戦闘経験値（熱の基準）。WN8 期待値表の
    expDamage / expFrag / expSpot を経験値に換算する。表に無ければ FALLBACK_XP_REF。
    """
    from src.wn8 import _expected
    exp = _expected().get(int(type_comp_descr)) if type_comp_descr else None
    if not exp:
        return FALLBACK_XP_REF
    return (XP_PER_DAMAGE * exp["expDamage"] + XP_PER_KILL * exp["expFrag"]
            + XP_PER_SPOT * exp["expSpot"])


# ---------------------------------------------------------------------------
# 曲線と切り出し
# ---------------------------------------------------------------------------

def build_curve(inputs: list[HeatInput], duration: float, xp_ref: float,
                dt: float = DT, half_life: float = HALF_LIFE_SEC) -> HeatCurve:
    """出来事で熱が上がり、半減期 half_life で冷めていく曲線を作る。"""
    n = max(int(duration / dt) + 1, 1)
    add = np.zeros(n)
    for h in inputs:
        i = int(round(h.timestamp / dt))
        if 0 <= i < n:
            add[i] += h.xp / xp_ref
    decay = 0.5 ** (dt / half_life)
    values = np.zeros(n)
    acc = 0.0
    for i in range(n):
        acc = acc * decay + add[i]
        values[i] = acc
    return HeatCurve(values, dt, xp_ref, inputs)


def hot_segments(curve: HeatCurve, threshold: float, lead: float = LEAD_SEC,
                 tail: float = TAIL_SEC, gap: float = MERGE_GAP_SEC,
                 exit_ratio: float = EXIT_RATIO) -> list[tuple[float, float]]:
    """
    見どころの区間（2つのしきい値 = ヒステリシス）。熱が「出るしきい値」
    （threshold × exit_ratio）以上の連続区間のうち、「入るしきい値」threshold に
    届いた山を含むものだけを採る。どの山を選ぶかは入るしきい値、区間の長さは
    出るしきい値で決まるので、入るぎりぎりの山でも細切れにならない。
    頭に lead、終わりに tail を足し、間が gap 未満の区間はつなぐ。
    """
    exit_th = threshold * exit_ratio
    warm = curve.values >= exit_th
    raw: list[tuple[float, float]] = []
    i, n = 0, len(warm)
    while i < n:
        if warm[i]:
            j = i
            while j + 1 < n and warm[j + 1]:
                j += 1
            if curve.values[i:j + 1].max() >= threshold:
                raw.append((i * curve.dt, (j + 1) * curve.dt))
            i = j + 1
        else:
            i += 1
    end_limit = n * curve.dt
    segs: list[tuple[float, float]] = []
    for a, b in raw:
        a, b = max(0.0, a - lead), min(end_limit, b + tail)
        if segs and a < segs[-1][1] + gap:
            segs[-1] = (segs[-1][0], max(segs[-1][1], b))
        else:
            segs.append((a, b))
    return [(round(a, 2), round(b, 2)) for a, b in segs]


def _total(segs: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in segs)


def select_segments(curve: HeatCurve, max_total: float, min_total: float = MIN_TOTAL_SEC,
                    threshold: float = THRESHOLD,
                    min_threshold: float = MIN_THRESHOLD) -> tuple[list[tuple[float, float]], float]:
    """
    しきい値を基準に区間を選ぶ。総尺が max_total を超えるならしきい値を上げて絞り
    （アツい部分ほど残る）、min_total に届かないなら min_threshold まで下げて広げる。
    Returns: (区間, 使ったしきい値)
    """
    segs = hot_segments(curve, threshold)
    if _total(segs) > max_total:
        lo, hi = threshold, float(curve.values.max())
        for _ in range(40):
            mid = (lo + hi) / 2
            if _total(hot_segments(curve, mid)) > max_total:
                lo = mid
            else:
                hi = mid
        threshold = hi
        segs = hot_segments(curve, threshold)
    elif _total(segs) < min_total and threshold > min_threshold:
        lo, hi = min_threshold, threshold
        if _total(hot_segments(curve, lo)) <= min_total:
            threshold = lo
        else:
            for _ in range(40):
                mid = (lo + hi) / 2
                if _total(hot_segments(curve, mid)) < min_total:
                    hi = mid
                else:
                    lo = mid
            threshold = lo
        segs = hot_segments(curve, threshold)
    return segs, round(threshold, 4)


# ---------------------------------------------------------------------------
# 確認用のグラフ画像（OpenCV で描く。matplotlib は使わない）
# ---------------------------------------------------------------------------

_KIND_COLORS = {  # BGR
    "kill": (60, 60, 230), "blind_kill": (200, 60, 200), "damage": (40, 170, 255), "hit": (40, 200, 255),
    "no_damage_hit": (200, 200, 200), "taken": (230, 140, 60),
}


def plot_curve(curve: HeatCurve, segments: list[tuple[float, float]], threshold: float,
               out_path: Path, width: int = 1600, height: int = 500) -> Path:
    import cv2
    pad_l, pad_r, pad_t, pad_b = 60, 20, 30, 50
    img = np.full((height, width, 3), 255, np.uint8)
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    duration = len(curve.values) * curve.dt
    vmax = max(float(curve.values.max()), threshold) * 1.1 or 1.0

    def x(t):
        return int(pad_l + t / duration * w)

    def y(v):
        return int(pad_t + h - v / vmax * h)

    for a, b in segments:
        cv2.rectangle(img, (x(a), pad_t), (x(b), pad_t + h), (210, 245, 210), -1)
    for t in range(0, int(duration) + 1, 30):
        cv2.line(img, (x(t), pad_t + h), (x(t), pad_t + h + 5), (0, 0, 0), 1)
        cv2.putText(img, f"{t // 60}:{t % 60:02d}", (x(t) - 18, pad_t + h + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    pts = np.array([[x(i * curve.dt), y(v)] for i, v in enumerate(curve.values)], np.int32)
    cv2.polylines(img, [pts], False, (30, 30, 30), 2, cv2.LINE_AA)
    cv2.line(img, (pad_l, y(threshold)), (pad_l + w, y(threshold)), (0, 0, 200), 1, cv2.LINE_AA)
    exit_th = threshold * EXIT_RATIO
    cv2.line(img, (pad_l, y(exit_th)), (pad_l + w, y(exit_th)), (0, 140, 255), 1, cv2.LINE_AA)
    cv2.putText(img, f"exit {exit_th:.3f}", (pad_l + 5, y(exit_th) - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 140, 255), 1, cv2.LINE_AA)
    cv2.putText(img, f"enter {threshold:.3f}", (pad_l + 5, y(threshold) - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 200), 1, cv2.LINE_AA)
    for hi in curve.inputs:
        color = _KIND_COLORS.get(hi.kind, (120, 120, 120))
        cv2.circle(img, (x(hi.timestamp), pad_t + h - 6), 5 if hi.kind in ("kill", "blind_kill") else 3, color, -1)
    cv2.rectangle(img, (pad_l, pad_t), (pad_l + w, pad_t + h), (0, 0, 0), 1)
    total = _total(segments)
    cv2.putText(img, f"heat (1.0 = avg battle combat XP {curve.xp_ref:.0f})   "
                     f"selected {len(segments)} segments / {total:.1f}s   "
                     f"dots: red=kill purple=blind kill orange=damage gray=no-pen blue=taken",
                (pad_l, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    out_path = Path(out_path)
    cv2.imwrite(str(out_path), img)
    return out_path


def load_heat(recording_path: Path, type_comp_descr: int | None, duration: float) -> HeatCurve | None:
    """録画のサイドカー（.meta.json / .events.json）から曲線を作る。v7 でなければ None。"""
    import json
    recording_path = Path(recording_path)
    try:
        meta = json.loads(recording_path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        data = json.loads(recording_path.with_suffix(".events.json").read_text(encoding="utf-8"))
        rec_start = float(meta["rec_start_epoch"])
    except (OSError, ValueError, KeyError):
        return None
    inputs = heat_inputs(data, rec_start)
    if inputs is None:
        return None
    return build_curve(inputs, duration, xp_reference(type_comp_descr))
