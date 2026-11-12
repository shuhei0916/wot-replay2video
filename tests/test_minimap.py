"""src/minimap.py（ミニマップ表示計画）のテスト。"""

from src.minimap import (
    TravelSegment,
    fit_travel_to_budget,
    plan_first_shot_overlay,
    plan_travel_segments,
    positions_from_events,
)


def _line_positions(t0, t1, speed_mps, x0=0.0):
    """t0..t1 を 1 秒刻みで x 方向に speed_mps で直進する位置列。"""
    return [(float(t), x0 + (t - t0) * speed_mps, 0.0) for t in range(int(t0), int(t1) + 1)]


class TestPositionsFromEvents:
    def test_converts_epoch_to_video_time(self):
        data = {"positions": [[1000.5, 10.0, 20.0], [999.0, 0.0, 0.0]]}
        assert positions_from_events(data, 1000.0) == [(0.5, 10.0, 20.0)]

    def test_missing_positions(self):
        assert positions_from_events({"events": []}, 0.0) == []

    def test_skips_malformed_rows(self):
        data = {"positions": [[1001.0, "x"], None, [1002.0, 1.0, 2.0]]}
        assert positions_from_events(data, 1000.0) == [(2.0, 1.0, 2.0)]


class TestPlanTravelSegments:
    def test_long_move_between_clips_gets_segment(self):
        # 10s〜70s に 10m/s で 600m 移動
        pos = [(t, 0.0, 0.0) for t in range(0, 10)] + _line_positions(10, 70, 10.0) + \
              [(t, 600.0, 0.0) for t in range(71, 100)]
        segs = plan_travel_segments([(0, 8), (80, 90)], pos)
        assert len(segs) == 1
        s = segs[0]
        assert s.after_clip == 0
        assert s.distance_m >= 500
        assert 4.0 <= s.speed <= 16.0
        assert s.out_duration <= 5.0 + 1e-6
        # 戦闘直前（移動の終わり）側が残る
        assert s.src_end == 70

    def test_short_move_is_skipped(self):
        pos = _line_positions(10, 20, 5.0)  # 50m
        assert plan_travel_segments([(0, 8), (30, 40)], pos) == []

    def test_standing_still_is_skipped(self):
        pos = [(float(t), 100.0, 100.0) for t in range(0, 100)]
        assert plan_travel_segments([(0, 8), (80, 90)], pos) == []

    def test_no_positions(self):
        assert plan_travel_segments([(0, 8), (80, 90)], []) == []

    def test_short_move_window_uses_min_speed(self):
        # 8 秒で 200m 移動 → 8/3 < 4 なので最低速度 4 倍、出力 2 秒
        pos = _line_positions(20, 28, 25.0)
        segs = plan_travel_segments([(0, 10), (40, 50)], pos)
        assert len(segs) == 1
        assert segs[0].speed == 4.0
        assert abs(segs[0].out_duration - 2.0) < 1e-6


class TestFitTravelToBudget:
    def test_drops_shortest_distance_first(self):
        a = TravelSegment(0, 0, 40, 8.0, 300.0)   # 5s
        b = TravelSegment(1, 0, 40, 8.0, 800.0)   # 5s
        kept = fit_travel_to_budget([a, b], clips_total_sec=142, max_total_sec=150)
        assert kept == [b]

    def test_keeps_all_when_fits(self):
        a = TravelSegment(0, 0, 40, 8.0, 300.0)
        assert fit_travel_to_budget([a], 100, 150) == [a]


class TestPlanFirstShotOverlay:
    def test_overlay_starts_just_after_first_visible_shot(self):
        clips = [(10.0, 20.0), (50.0, 60.0)]
        assert plan_first_shot_overlay(clips, [5.0, 13.0, 55.0]) == (0, 3.3, 6.3)

    def test_shot_near_clip_end_shows_before(self):
        clips = [(10.0, 20.0)]
        i, a, b = plan_first_shot_overlay(clips, [19.8])
        assert i == 0 and b == 10.0 and a == 7.0

    def test_no_shot_in_clips(self):
        assert plan_first_shot_overlay([(10.0, 20.0)], [30.0]) is None
