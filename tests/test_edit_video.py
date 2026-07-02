"""
edit_video モジュールの純粋ロジックのテスト。

ffmpeg を使う clip_and_crop / make_shorts は対象外とし、
クリップ選択ロジック（_dedup_clips / select_clips）を検証する。
"""

from src.detect_highlights import HighlightEvent
from src.edit_video import (
    CLIP_POST_SEC,
    CLIP_PRE_SEC,
    SHORTS_MAX_SEC,
    _dedup_clips,
    select_clips,
)

CLIP_DURATION = CLIP_PRE_SEC + CLIP_POST_SEC


def _event(timestamp: float, score: float = 0.5) -> HighlightEvent:
    return HighlightEvent(timestamp=timestamp, event_type="shot_flash", score=score)


# ---- _dedup_clips: クリップ範囲の重複除去 ----

class TestDedupClips:
    def test_empty(self):
        assert _dedup_clips([]) == []

    def test_far_apart_events_all_kept(self):
        events = [_event(10.0), _event(100.0), _event(200.0)]
        assert len(_dedup_clips(events)) == 3

    def test_overlapping_events_keep_higher_score(self):
        low = _event(10.0, score=0.2)
        high = _event(12.0, score=0.9)  # 10s のクリップ範囲 (7-14s) と重なる
        kept = _dedup_clips([low, high])
        assert kept == [high]

    def test_adjacent_but_not_overlapping_kept(self):
        # クリップは [t-3, t+4] なので 7 秒離れていれば重ならない
        events = [_event(10.0), _event(10.0 + CLIP_DURATION)]
        assert len(_dedup_clips(events)) == 2


# ---- select_clips: 60 秒上限 + 時系列順 ----

class TestSelectClips:
    def test_empty(self):
        assert select_clips([]) == []

    def test_result_sorted_by_timestamp(self):
        events = [_event(200.0, 0.9), _event(50.0, 0.5), _event(120.0, 0.7)]
        selected = select_clips(events)
        timestamps = [e.timestamp for e in selected]
        assert timestamps == sorted(timestamps)

    def test_total_duration_within_shorts_limit(self):
        # 20 イベント（重複なし）を入れても合計が上限以内に収まる本数に絞られる
        events = [_event(i * 60.0, score=0.5) for i in range(20)]
        selected = select_clips(events)
        assert len(selected) * CLIP_DURATION <= SHORTS_MAX_SEC

    def test_highest_scores_survive_cap(self):
        # 上限を超える本数がある場合、スコア上位が優先される
        max_clips = int(SHORTS_MAX_SEC // CLIP_DURATION)
        n = max_clips + 5
        events = [_event(i * 60.0, score=(i + 1) / n) for i in range(n)]
        selected = select_clips(events)
        scores = sorted((e.score for e in selected), reverse=True)
        expected_top = sorted((e.score for e in events), reverse=True)[:max_clips]
        assert scores == expected_top

    def test_custom_max_total_sec(self):
        events = [_event(i * 60.0, score=0.5) for i in range(10)]
        selected = select_clips(events, max_total_sec=CLIP_DURATION * 2)
        assert len(selected) == 2

    def test_at_least_one_clip_even_with_tiny_limit(self):
        events = [_event(10.0)]
        selected = select_clips(events, max_total_sec=1.0)
        assert len(selected) == 1
