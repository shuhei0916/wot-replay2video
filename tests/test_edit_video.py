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
    build_filter_args,
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


# ---- 可変長クリップ（pre_sec/post_sec 指定あり）----

def _paced_event(timestamp: float, pre: float, post: float, score: float = 0.5) -> HighlightEvent:
    return HighlightEvent(
        timestamp=timestamp, event_type="shot_mod", score=score, pre_sec=pre, post_sec=post,
    )


class TestVariableLengthClips:
    def test_dedup_uses_own_pre_post_not_fixed(self):
        # 短いクリップ(pre=1,post=1)同士は6秒離れていれば重ならないが、
        # 固定長(CLIP_DURATION=7秒)基準だと重なってしまう距離
        a = _paced_event(10.0, pre=1.0, post=1.0, score=0.5)
        b = _paced_event(16.0, pre=1.0, post=1.0, score=0.9)
        assert len(_dedup_clips([a, b])) == 2

    def test_dedup_long_clip_still_overlaps_at_same_distance(self):
        a = _paced_event(10.0, pre=3.0, post=4.0, score=0.5)
        b = _paced_event(16.0, pre=3.0, post=4.0, score=0.9)
        kept = _dedup_clips([a, b])
        assert kept == [b]

    def test_select_clips_fits_more_short_clips_in_budget(self):
        events = [_paced_event(i * 20.0, pre=1.0, post=1.0) for i in range(10)]
        selected = select_clips(events, max_total_sec=10.0)
        assert len(selected) == 5

    def test_select_clips_skips_oversized_high_score_for_smaller_ones(self):
        # 最高スコアのクリップが尺オーバーでも、後続の小さいクリップは拾われる
        huge = _paced_event(10.0, pre=50.0, post=50.0, score=1.0)
        small = _paced_event(200.0, pre=1.0, post=1.0, score=0.5)
        selected = select_clips([huge, small], max_total_sec=10.0)
        assert selected == [small]

    def test_mixed_fixed_and_variable_events(self):
        fixed = _event(10.0, score=0.9)  # pre_sec/post_sec None → 既定値 (7秒)
        variable = _paced_event(100.0, pre=1.0, post=1.0, score=0.5)
        selected = select_clips([fixed, variable], max_total_sec=100.0)
        assert len(selected) == 2


# ---- build_filter_args: フィルタ引数の組み立て ----

HP_OVERLAY = {
    "enabled": True,
    "src": {"x": 3, "y": 873, "w": 224, "h": 18},
    "dst": {"w": 874, "y": 300},
}


class TestBuildFilterArgs:
    def test_without_overlay_uses_vf(self):
        args = build_filter_args(1920, 1080, None)
        assert args == ["-vf", "crop=607:1080:656:0,scale=1080:1920"]

    def test_with_overlay_uses_filter_complex(self):
        flag, graph = build_filter_args(1920, 1080, HP_OVERLAY)
        assert flag == "-filter_complex"
        # 中央 9:16 クロップは overlay 有無で変わらない
        assert "crop=607:1080:656:0,scale=1080:1920" in graph
        # HP バーの切り出し矩形
        assert "crop=224:18:3:873" in graph
        # 高さはアスペクト比維持: 18 * 874 / 224 = 70.2 → 70
        assert "scale=874:70:flags=lanczos" in graph
        # 中央寄せ: (1080 - 874) // 2 = 103
        assert "overlay=103:300" in graph

    def test_overlay_dst_height_keeps_aspect(self):
        cfg = {
            "enabled": True,
            "src": {"x": 0, "y": 0, "w": 100, "h": 20},
            "dst": {"w": 500, "y": 100},
        }
        _, graph = build_filter_args(1920, 1080, cfg)
        assert "scale=500:100:flags=lanczos" in graph  # 20 * 500/100 = 100
        assert "overlay=290:100" in graph              # (1080-500)//2 = 290
