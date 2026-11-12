"""
ミニマップ表示の計画（純粋ロジック）。

視聴者が「どのマップのどこで戦っているか」を学べるよう、2種類の表示を入れる:

  (a) 初弾マップ: 最初の射撃のクリップで、元映像右下のミニマップを
      縦動画に数秒重ねる（[[plan_first_shot_overlay]]）。
  (b) 移動の早送り: クリップ間で自車が大きく移動していたら、その区間を
      早送りしてミニマップを大写しにしたつなぎを挟む（[[plan_travel_segments]]）。
      自車位置は mod v5 が 1 秒ごとに記録する positions を使う。

ffmpeg のフィルタ組み立ては edit_video 側。ここはテスト可能な判定だけを持つ。
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from src.config import load_config

# 1サンプル（約1秒）でこれ以上動いていれば「移動中」とみなす（停車中の揺れ・砲塔旋回を除外）
MOVING_STEP_M = 2.0


@dataclass
class TravelSegment:
    after_clip: int      # このクリップ（selected のインデックス）の直後に挿入する
    src_start: float     # 元動画での開始秒
    src_end: float       # 元動画での終了秒
    speed: float         # 再生速度倍率
    distance_m: float    # 区間内の移動距離（メートル）

    @property
    def out_duration(self) -> float:
        return (self.src_end - self.src_start) / self.speed


def minimap_config() -> dict | None:
    """config.yaml の shorts.minimap を返す。無効・未設定なら None。"""
    cfg = (load_config().get("shorts") or {}).get("minimap") or {}
    if not cfg.get("enabled") or not cfg.get("src"):
        return None
    return cfg


def positions_from_events(data: dict, rec_start_epoch: float) -> list[tuple[float, float, float]]:
    """mod 出力の positions（[epoch, x, z]）を動画内秒数 (t, x, z) に変換する。"""
    out = []
    for row in data.get("positions") or []:
        try:
            epoch, x, z = float(row[0]), float(row[1]), float(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        t = epoch - rec_start_epoch
        if t >= 0:
            out.append((round(t, 2), x, z))
    return sorted(out)


def load_positions(recording_path: Path) -> list[tuple[float, float, float]]:
    """録画のサイドカー（.meta.json / .events.json）から自車位置列を読む。無ければ []。"""
    recording_path = Path(recording_path)
    try:
        meta = json.loads(recording_path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        data = json.loads(recording_path.with_suffix(".events.json").read_text(encoding="utf-8"))
        return positions_from_events(data, float(meta["rec_start_epoch"]))
    except (OSError, ValueError, KeyError):
        return []


def _moving_window(
    positions: list[tuple[float, float, float]], t0: float, t1: float
) -> tuple[float, float, float] | None:
    """
    [t0, t1] 内で実際に動いていた範囲 (最初に動いた時刻, 最後に動いた時刻, 移動距離) を返す。
    動いていなければ None。
    """
    pts = [p for p in positions if t0 <= p[0] <= t1]
    first = last = None
    dist = 0.0
    for (ta, xa, za), (tb, xb, zb) in zip(pts, pts[1:]):
        step = math.hypot(xb - xa, zb - za)
        if step >= MOVING_STEP_M:
            dist += step
            if first is None:
                first = ta
            last = tb
    if first is None:
        return None
    return first, last, dist


def plan_travel_segments(
    clip_windows: list[tuple[float, float]],
    positions: list[tuple[float, float, float]],
    min_distance_m: float = 150.0,
    target_sec: float = 3.0,
    min_speed: float = 4.0,
    max_speed: float = 16.0,
    max_out_sec: float = 5.0,
) -> list[TravelSegment]:
    """
    クリップ間の移動区間から、早送りつなぎを挿入する計画を立てる。

    Args:
        clip_windows: 採用クリップの (元動画開始秒, 終了秒)。時系列順
        positions: 自車位置 (t, x, z)
        min_distance_m: この距離以上動いた区間だけ早送りを入れる
        target_sec: 早送りの出力尺の目標
        min_speed/max_speed: 速度倍率の範囲（遅すぎると間延び、速すぎると追えない）
        max_out_sec: 出力尺の上限。最高速でも収まらない長い移動は、
            次の戦闘直前の部分（どこへ向かったかが分かる部分）だけ使う
    """
    segments: list[TravelSegment] = []
    for i, ((_, end_a), (start_b, _)) in enumerate(zip(clip_windows, clip_windows[1:])):
        if start_b - end_a <= 0:
            continue
        mw = _moving_window(positions, end_a, start_b)
        if mw is None:
            continue
        m_start, m_end, dist = mw
        if dist < min_distance_m:
            continue
        span = m_end - m_start
        speed = min(max(span / target_sec, min_speed), max_speed)
        if span / speed > max_out_sec:
            m_start = m_end - max_out_sec * speed
            span = m_end - m_start
        segments.append(TravelSegment(
            after_clip=i, src_start=round(m_start, 2), src_end=round(m_end, 2),
            speed=round(speed, 2), distance_m=round(dist, 1),
        ))
    return segments


def fit_travel_to_budget(
    segments: list[TravelSegment], clips_total_sec: float, max_total_sec: float
) -> list[TravelSegment]:
    """合計尺が上限を超えるなら、移動距離の短い早送りから落とす。"""
    kept = sorted(segments, key=lambda s: s.distance_m, reverse=True)
    while kept and clips_total_sec + sum(s.out_duration for s in kept) > max_total_sec:
        kept.pop()
    return sorted(kept, key=lambda s: s.after_clip)


def plan_first_shot_overlay(
    clip_windows: list[tuple[float, float]],
    shot_times: list[float],
    duration_sec: float = 3.0,
    lead_sec: float = 0.3,
) -> tuple[int, float, float] | None:
    """
    初弾マップを重ねるクリップと、そのクリップ内での表示区間を決める。

    最初に採用クリップ内に入っている射撃（＝動画で最初に見える射撃）の
    直後から duration_sec 表示する。クリップ末尾を超える分は切り詰める。

    Returns:
        (クリップのインデックス, クリップ内の表示開始秒, 表示終了秒)。射撃が無ければ None
    """
    for t in sorted(shot_times):
        for i, (start, end) in enumerate(clip_windows):
            if start <= t <= end:
                show_from = max(0.0, t - start + lead_sec)
                show_to = min(end - start, show_from + duration_sec)
                if show_to - show_from < 1.0:
                    # クリップ末尾ぎりぎりの射撃なら、射撃前から見せる
                    show_from = max(0.0, show_to - duration_sec)
                return i, round(show_from, 2), round(show_to, 2)
    return None
