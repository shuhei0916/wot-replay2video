"""
mod (mod_shot_logger) が記録したゲーム内イベントをハイライトイベントに変換する。

mod はイベントを壁時計時刻 (epoch) で記録する。録画開始 epoch
（pipeline が録画ごとに <recording>.meta.json へ保存）との差分で
動画内タイムスタンプに変換する。

CV 検出（輝度・音声）と違い推測を含まない正確なイベントのため、
存在する場合は最優先で使う。

被弾 (hit_taken) イベントは2通りに使う:
  (a) 撃ち合いボーナス: 自分の射撃直後に被弾していれば、一方的な射撃より
      緊迫感がある「撃ち合い」とみなしその射撃イベントのスコアを加点する。
  (b) 大きな一撃の単独ハイライト化: 残 HP を大きく削る一撃（マスト外し・
      弾薬庫誘爆等）は、撃ち合いの有無に関わらずそれ自体をハイライト
      候補にする。
"""

import json
import random
from pathlib import Path

from src.detect_highlights import HighlightEvent
from src.edit_video import CLIP_POST_SEC, CLIP_PRE_SEC

# mod イベントの基礎スコア。近傍に音声ピークがあれば加点する
BASE_SCORE = 0.7
AUDIO_BONUS_MAX = 0.3
AUDIO_MATCH_WINDOW_SEC = 0.6

# 被弾イベントの扱い
HIT_TAKEN_MIN_PCT = 0.3     # 残HPのこの割合以上を一撃で失ったら単独ハイライト候補にする
HIT_TAKEN_BASE_SCORE = 0.75
DUEL_BONUS = 0.15           # 射撃直後に被弾していた（撃ち合い）場合の加点
DUEL_WINDOW_SEC = 3.0       # 射撃からこの秒数以内の被弾を「撃ち合い」とみなす

# 可変長クリップ: 直前の自分の射撃からの経過秒（reload interval）でクリップ長を決める。
# 車両ごとのDPM静的テーブルではなく、そのリプレイで実際に観測した間隔を使う
# （装填ブースト等の実際の挙動に追従し、新車両追加時のメンテも不要なため）
FAST_RELOAD_SEC = 4.0    # この間隔以下 → 最短クリップ（自動装填・機関砲想定）
SLOW_RELOAD_SEC = 20.0   # この間隔以上 → 最長クリップ（重戦車・駆逐の単発想定）
PRE_SEC_RANGE = (1.5, 4.5)
POST_SEC_RANGE = (2.0, 6.0)
JITTER_RATIO = 0.2       # 基準値の ±20% をランダムに乗せる（絶対秒数固定だと短尺クリップで
                         # 相対的に振れ幅が大きすぎ、長尺クリップでは気づかないほど小さくなるため比率にする）


def _lerp_clamped(x: float, lo_in: float, hi_in: float, lo_out: float, hi_out: float) -> float:
    """x を [lo_in, hi_in] から [lo_out, hi_out] へ線形補間する（範囲外はクランプ）。"""
    t = (x - lo_in) / (hi_in - lo_in)
    t = max(0.0, min(1.0, t))
    return lo_out + t * (hi_out - lo_out)


def pacing_for_interval(interval: float | None, rng: random.Random) -> tuple[float, float]:
    """
    直前の自分の射撃からの経過秒（reload interval）からクリップの (pre_sec, post_sec) を決める。

    interval が None（そのリプレイで最初の射撃など、直前射撃が無い）場合は
    CLIP_PRE_SEC/CLIP_POST_SEC を基準値にする。速い（間隔が短い）ほど短く、
    遅いほど長くなるよう線形補間し、基準値に対して ±JITTER_RATIO の比率ジッターを
    常にかける（interval 不明のケースも含め、毎回寸分違わず同じ長さにはしない）。
    """
    if interval is None:
        base_pre, base_post = CLIP_PRE_SEC, CLIP_POST_SEC
    else:
        base_pre = _lerp_clamped(interval, FAST_RELOAD_SEC, SLOW_RELOAD_SEC, *PRE_SEC_RANGE)
        base_post = _lerp_clamped(interval, FAST_RELOAD_SEC, SLOW_RELOAD_SEC, *POST_SEC_RANGE)

    pre = base_pre * (1 + rng.uniform(-JITTER_RATIO, JITTER_RATIO))
    post = base_post * (1 + rng.uniform(-JITTER_RATIO, JITTER_RATIO))
    return round(pre, 2), round(post, 2)


def convert_events(
    data: dict,
    rec_start_epoch: float,
    max_ts: float | None = None,
    dynamic_pacing: bool = True,
) -> list[HighlightEvent]:
    """
    mod 出力 (shot_events.json の内容) を動画内タイムスタンプのイベントに変換する。

    Args:
        data: {"events": [{"epoch": float, "type": str, ...}, ...]}
              type "shot" は射撃、"hit_taken" は被弾（damage_pct を伴うことがある）
        rec_start_epoch: 録画開始の壁時計時刻
        max_ts: 動画の長さ（秒）。指定時は範囲外イベントを除外
        dynamic_pacing: True なら射撃間隔に応じて shot_mod の pre_sec/post_sec を
            可変にする（[[pacing_for_interval]]）。False なら従来通り固定長
            （pre_sec/post_sec は None のまま、呼び出し側の既定値を使う）
    """
    shots: list[HighlightEvent] = []
    hits: list[tuple[float, float | None]] = []

    for e in data.get("events", []):
        et = e.get("type")
        if et not in ("shot", "hit_taken"):
            continue
        ts = round(float(e["epoch"]) - rec_start_epoch, 2)
        if ts < 0:
            continue
        if max_ts is not None and ts > max_ts:
            continue
        if et == "shot":
            shots.append(HighlightEvent(timestamp=ts, event_type="shot_mod", score=BASE_SCORE))
        else:
            hits.append((ts, e.get("damage_pct")))

    if dynamic_pacing and shots:
        rng = random.Random()
        shots = sorted(shots, key=lambda e: e.timestamp)
        prev_ts: float | None = None
        paced_shots = []
        for s in shots:
            interval = None if prev_ts is None else round(s.timestamp - prev_ts, 2)
            prev_ts = s.timestamp
            pre, post = pacing_for_interval(interval, rng)
            paced_shots.append(HighlightEvent(
                timestamp=s.timestamp, event_type=s.event_type, score=s.score,
                pre_sec=pre, post_sec=post,
            ))
        shots = paced_shots

    # (a) 撃ち合いボーナス: 射撃直後の被弾があればスコアを加点する
    hit_timestamps = [ts for ts, _ in hits]
    boosted_shots = []
    for s in shots:
        is_duel = any(0 <= ts - s.timestamp <= DUEL_WINDOW_SEC for ts in hit_timestamps)
        if is_duel:
            boosted_shots.append(HighlightEvent(
                timestamp=s.timestamp, event_type=s.event_type,
                score=round(min(s.score + DUEL_BONUS, 1.0), 3),
                pre_sec=s.pre_sec, post_sec=s.post_sec,
            ))
        else:
            boosted_shots.append(s)

    # (b) 残HPを大きく削る一撃は単独のハイライト候補にする
    hit_events = [
        HighlightEvent(timestamp=ts, event_type="hit_taken", score=HIT_TAKEN_BASE_SCORE)
        for ts, pct in hits
        if pct is not None and pct >= HIT_TAKEN_MIN_PCT
    ]

    return sorted(boosted_shots + hit_events, key=lambda e: e.timestamp)


def score_with_audio(
    mod_events: list[HighlightEvent],
    audio_events: list[HighlightEvent],
    window_sec: float = AUDIO_MATCH_WINDOW_SEC,
) -> list[HighlightEvent]:
    """
    mod イベントのスコアを近傍の音声ピーク強度で重み付けする。
    クリップ数が上限を超えたときの選抜（select_clips）で
    「大きな砲撃音のショット」を優先させるため。
    """
    scored = []
    for m in mod_events:
        bonus = 0.0
        for a in audio_events:
            if abs(a.timestamp - m.timestamp) <= window_sec:
                bonus = max(bonus, AUDIO_BONUS_MAX * a.score)
        scored.append(HighlightEvent(
            timestamp=m.timestamp,
            event_type=m.event_type,
            score=round(min(m.score + bonus, 1.0), 3),
            pre_sec=m.pre_sec, post_sec=m.post_sec,
        ))
    return scored


def load_mod_events(
    recording_path: Path,
    dynamic_pacing: bool = True,
) -> list[HighlightEvent] | None:
    """
    録画のサイドカーファイル（.meta.json / .events.json）から
    mod イベントを読み込む。どちらかが無い・壊れている場合は None
    （呼び出し側は CV 検出にフォールバックする）。
    """
    recording_path = Path(recording_path)
    meta_path = recording_path.with_suffix(".meta.json")
    events_path = recording_path.with_suffix(".events.json")
    if not meta_path.exists() or not events_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        data = json.loads(events_path.read_text(encoding="utf-8"))
        rec_start = float(meta["rec_start_epoch"])
    except (ValueError, KeyError, OSError):
        return None
    events = convert_events(data, rec_start, dynamic_pacing=dynamic_pacing)
    return events or None
