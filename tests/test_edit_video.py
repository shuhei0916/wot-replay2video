"""
edit_video モジュールの純粋ロジックのテスト。

ffmpeg を使う clip_and_crop / make_shorts は対象外とし、
場面単位の選択ロジック（build_scenes / trim_scene / select_scenes）を検証する。
"""

from src.detect_highlights import HighlightEvent
from src.edit_video import (
    CLIP_POST_SEC,
    CLIP_PRE_SEC,
    SHORTS_MAX_SEC,
    build_filter_args,
    build_scenes,
    select_scenes,
    trim_scene,
)

CLIP_DURATION = CLIP_PRE_SEC + CLIP_POST_SEC


def _event(timestamp: float, score: float = 0.5) -> HighlightEvent:
    return HighlightEvent(timestamp=timestamp, event_type="shot_flash", score=score)


def _paced_event(timestamp: float, pre: float, post: float, score: float = 0.5) -> HighlightEvent:
    return HighlightEvent(
        timestamp=timestamp, event_type="shot_mod", score=score, pre_sec=pre, post_sec=post,
    )


# ---- build_scenes: 重なるイベントを1つの場面にまとめる（2026-11-27） ----

class TestBuildScenes:
    def test_empty(self):
        assert build_scenes([]) == []

    def test_overlapping_events_merge_into_one_scene(self):
        # 100s（97-104s）と 105s（102-109s）は重なる → 97-109s の1場面に両方入る
        scenes = build_scenes([_event(105.0, 0.9), _event(100.0, 0.2)])
        assert len(scenes) == 1
        assert (scenes[0].start, scenes[0].end) == (97.0, 109.0)
        assert [e.timestamp for e in scenes[0].events] == [100.0, 105.0]

    def test_small_gap_merges_large_gap_splits(self):
        # 窓は [t-3, t+4]。10s の窓の終わりは 14s
        near = build_scenes([_event(10.0), _event(17.5)])   # 次の窓は 14.5s から（間 0.5s）
        far = build_scenes([_event(10.0), _event(18.5)])    # 次の窓は 15.5s から（間 1.5s）
        assert len(near) == 1 and len(far) == 2

    def test_chain_merges_transitively(self):
        scenes = build_scenes([_event(10.0), _event(15.0), _event(20.0)])
        assert len(scenes) == 1 and scenes[0].end == 24.0

    def test_window_clamped_at_zero(self):
        assert build_scenes([_event(1.0)])[0].start == 0.0

    def test_uses_variable_pre_post(self):
        a = _paced_event(10.0, pre=1.0, post=1.0)
        b = _paced_event(16.0, pre=1.0, post=1.0)  # 固定長なら重なる距離だが短窓なので別場面
        assert len(build_scenes([a, b])) == 2

    def test_scene_score_rewards_extra_highlights(self):
        single = build_scenes([_event(10.0, 0.7)])[0]
        double = build_scenes([_event(10.0, 0.7), _event(13.0, 0.6)])[0]
        with_miss = build_scenes([_event(10.0, 0.7), _event(13.0, 0.25)])[0]
        assert single.score == 0.7
        assert double.score == 0.8            # 見どころが1つ増えて +0.1
        assert with_miss.score == 0.7         # 何も起きない外れ（0.5 未満）は数えない

    def test_scene_bonus_capped(self):
        sc = build_scenes([_event(10.0 + i * 3, 0.7) for i in range(10)])[0]
        assert sc.score == 1.0  # 0.7 + 上限 0.3


# ---- trim_scene: 長すぎる場面を端から削る ----

class TestTrimScene:
    def test_drops_lower_scored_edge(self):
        sc = build_scenes([_event(10.0, 0.3), _event(14.0, 0.9), _event(18.0, 0.8)])[0]  # 7-22s
        trimmed = trim_scene(sc, 12.0)
        assert [e.timestamp for e in trimmed.events] == [14.0, 18.0]
        assert (trimmed.start, trimmed.end) == (11.0, 22.0)

    def test_keeps_best_until_fits(self):
        sc = build_scenes([_event(10.0, 0.5), _event(14.0, 0.9), _event(18.0, 0.4)])[0]
        trimmed = trim_scene(sc, 7.0)
        assert [e.timestamp for e in trimmed.events] == [14.0]

    def test_single_event_unchanged(self):
        sc = build_scenes([_event(10.0)])[0]
        assert trim_scene(sc, 1.0).duration == CLIP_DURATION


# ---- select_scenes: 尺の上限 + 時系列順 ----

class TestSelectScenes:
    def test_empty(self):
        assert select_scenes([]) == []

    def test_result_sorted_by_time(self):
        scenes = select_scenes([_event(200.0, 0.9), _event(50.0, 0.5), _event(120.0, 0.7)])
        starts = [s.start for s in scenes]
        assert starts == sorted(starts)

    def test_total_within_limit(self):
        scenes = select_scenes([_event(i * 60.0) for i in range(30)])
        assert sum(s.duration for s in scenes) <= SHORTS_MAX_SEC

    def test_highest_scores_survive(self):
        events = [_event(i * 60.0, score=round((i + 1) / 10, 1)) for i in range(10)]
        scenes = select_scenes(events, max_total_sec=CLIP_DURATION * 3)
        assert sorted(s.top.score for s in scenes) == [0.8, 0.9, 1.0]

    def test_overlapping_second_shot_is_kept(self):
        # 旧方式では低スコア側（105s の撃破かもしれない射撃）が捨てられていた
        scenes = select_scenes([_event(100.0, 0.9), _event(105.0, 0.5)], max_total_sec=40.0)
        assert len(scenes) == 1 and len(scenes[0].events) == 2

    def test_long_scene_trimmed_into_remaining_budget(self):
        brawl = [_event(100.0 + i * 4, 0.6) for i in range(8)]   # 97-132s の 35 秒の場面
        brawl[3] = _event(112.0, 0.95)
        scenes = select_scenes(brawl, max_total_sec=20.0)
        assert len(scenes) == 1
        assert scenes[0].duration <= 20.0
        assert 112.0 in [e.timestamp for e in scenes[0].events]   # 一番の見どころは残る

    def test_oversized_single_skipped_for_smaller(self):
        huge = _paced_event(10.0, pre=50.0, post=50.0, score=1.0)
        small = _paced_event(200.0, pre=1.0, post=1.0, score=0.5)
        scenes = select_scenes([huge, small], max_total_sec=10.0)
        assert [s.top for s in scenes] == [small]

    def test_at_least_one_scene_with_tiny_limit(self):
        assert len(select_scenes([_event(10.0)], max_total_sec=1.0)) == 1

    def test_fits_more_short_scenes(self):
        events = [_paced_event(i * 20.0, pre=1.0, post=1.0) for i in range(10)]
        assert len(select_scenes(events, max_total_sec=10.0)) == 5


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


MINIMAP = {
    "src": {"x": 1400, "y": 560, "w": 520, "h": 520},
    "dst": {"w": 700, "y": 1000},
    "start": 1.5,
    "end": 4.5,
}


class TestMinimapOverlay:
    def test_minimap_only(self):
        flag, graph = build_filter_args(1920, 1080, None, MINIMAP)
        assert flag == "-filter_complex"
        assert "split=2[main][mm]" in graph
        assert "crop=520:520:1400:560" in graph
        assert "scale=700:700:flags=lanczos" in graph
        # 中央寄せ (1080-700)//2 = 190、表示区間だけ有効
        assert "overlay=190:1000:enable='between(t,1.5,4.5)'" in graph
        # 最後のフィルタは出力ラベルを持たない（そのまま出力に流れる）
        assert not graph.rstrip().endswith("]")

    def test_hp_and_minimap_together(self):
        _, graph = build_filter_args(1920, 1080, HP_OVERLAY, MINIMAP)
        assert "split=3[main][hud][mm]" in graph
        assert "overlay=103:300[bg1]" in graph
        assert "[bg1][map]overlay=190:1000" in graph


class TestTravelFilter:
    def test_speed_and_label(self):
        from src.edit_video import build_travel_filter
        graph = build_travel_filter(1920, 1080, MINIMAP["src"], 8.0)
        assert graph.startswith("[0:v]setpts=PTS/8.0,")
        assert "▶▶" in graph  # ▶▶ のみ（文字は入れない）
        assert "早送り" not in graph
        assert "crop=520:520:1400:560" in graph
        assert graph.endswith("[out]")

    def test_blur_fade_at_end(self):
        # 最後の 0.4 秒でミニマップとマークを消し、ぼかし層を透明にして鮮明な映像へ
        from src.edit_video import build_travel_filter
        graph = build_travel_filter(1920, 1080, MINIMAP["src"], 8.0, out_duration=3.0, blur_fade_sec=0.4)
        assert "split=3[v][mm][sh]" in graph
        assert "enable='lt(t,2.6)'" in graph
        assert "fade=t=out:st=2.6:d=0.4:alpha=1[blur]" in graph
        assert "[sharp][blur]overlay=0:0" in graph
        assert graph.endswith("[out]")

    def test_blur_fade_skipped_when_clip_too_short(self):
        from src.edit_video import build_travel_filter
        graph = build_travel_filter(1920, 1080, MINIMAP["src"], 8.0, out_duration=0.3, blur_fade_sec=0.4)
        assert "fade=" not in graph and "split=2[v][mm]" in graph


# ---- 尺の上限と A/B テスト（2026-11-27） ----

class TestShortsBudget:
    def test_default(self, monkeypatch):
        import src.edit_video as ev
        monkeypatch.setattr(ev, "load_config", lambda: {})
        assert ev.shorts_budget_sec("x") == ev.SHORTS_MAX_SEC

    def test_config_value(self, monkeypatch):
        import src.edit_video as ev
        monkeypatch.setattr(ev, "load_config", lambda: {"shorts": {"max_total_sec": 45}})
        assert ev.shorts_budget_sec("x") == 45

    def test_ab_is_stable_and_mixes(self, monkeypatch):
        import src.edit_video as ev
        cfg = {"shorts": {"max_total_sec": 99, "length_test": {"enabled": True, "variants": [40, 150]}}}
        monkeypatch.setattr(ev, "load_config", lambda: cfg)
        stems = [f"20261127_{i:04d}_tank_map_20261127_120000" for i in range(40)]
        picks = [ev.shorts_budget_sec(s) for s in stems]
        assert set(picks) == {40, 150}                       # 両方に振り分けられる
        assert picks == [ev.shorts_budget_sec(s) for s in stems]  # 同じ録画は常に同じ側

    def test_ab_disabled_uses_max(self, monkeypatch):
        import src.edit_video as ev
        cfg = {"shorts": {"max_total_sec": 60, "length_test": {"enabled": False, "variants": [40]}}}
        monkeypatch.setattr(ev, "load_config", lambda: cfg)
        assert ev.shorts_budget_sec("x") == 60
