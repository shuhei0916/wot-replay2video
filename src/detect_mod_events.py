"""
mod (mod_shot_logger) が記録したゲーム内イベントをハイライトイベントに変換する。

mod はイベントを壁時計時刻 (epoch) で記録する。録画開始 epoch
（pipeline が録画ごとに <recording>.meta.json へ保存）との差分で
動画内タイムスタンプに変換する。

CV 検出（輝度・音声）と違い推測を含まない正確なイベントのため、
存在する場合は最優先で使う。
"""

import json
from pathlib import Path

from src.detect_highlights import HighlightEvent

# mod イベントの基礎スコア。近傍に音声ピークがあれば加点する
BASE_SCORE = 0.7
AUDIO_BONUS_MAX = 0.3
AUDIO_MATCH_WINDOW_SEC = 0.6


def convert_events(
    data: dict,
    rec_start_epoch: float,
    max_ts: float | None = None,
) -> list[HighlightEvent]:
    """
    mod 出力 (shot_events.json の内容) を動画内タイムスタンプのイベントに変換する。

    Args:
        data: {"events": [{"epoch": float, "type": str}, ...]}
        rec_start_epoch: 録画開始の壁時計時刻
        max_ts: 動画の長さ（秒）。指定時は範囲外イベントを除外
    """
    events = []
    for e in data.get("events", []):
        if e.get("type") != "shot":
            continue
        ts = round(float(e["epoch"]) - rec_start_epoch, 2)
        if ts < 0:
            continue
        if max_ts is not None and ts > max_ts:
            continue
        events.append(HighlightEvent(
            timestamp=ts, event_type="shot_mod", score=BASE_SCORE,
        ))
    return sorted(events, key=lambda e: e.timestamp)


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
        ))
    return scored


def load_mod_events(recording_path: Path) -> list[HighlightEvent] | None:
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
    events = convert_events(data, rec_start)
    return events or None
