"""src.hp_bar（HP バーの描き直し）のテスト。"""

import json
from pathlib import Path

from src.hp_bar import (
    COLOR_DRAIN,
    COLOR_HIGH,
    COLOR_LOW,
    COLOR_MID,
    HpTimeline,
    build_hp_bar_filters,
    hp_timeline_from_events,
)

REC = 1000.0
DST = {"w": 874, "y": 300}
V8 = Path(__file__).parent / "fixtures" / "mod_v8_t29_karelia.events.json"


def _data(events):
    return {"events": [dict(e, epoch=REC + e["t"]) for e in events]}


class TestTimeline:
    def test_v8_uses_recorded_health(self):
        data = _data([{"t": 50, "type": "hit_taken", "damage": 300, "health": 1180, "max_health": 1480}])
        tl = hp_timeline_from_events(data, REC, None)
        assert tl.max_hp == 1480
        assert tl.hp_at(49.9) == 1480 and tl.hp_at(50.0) == 1180

    def test_v7_subtracts_damage_from_battle_result_max(self):
        data = _data([{"t": 10, "type": "hit_taken", "damage": 200},
                      {"t": 20, "type": "hit_taken", "damage": 900}])
        tl = hp_timeline_from_events(data, REC, 1000)
        assert tl.changes == [(10.0, 800.0), (20.0, 0.0)]   # 0 未満にはならない

    def test_v7_infers_max_from_damage_pct(self):
        data = _data([{"t": 10, "type": "hit_taken", "damage": 300, "damage_pct": 0.25}])
        tl = hp_timeline_from_events(data, REC, None)
        assert tl.max_hp == 1200 and tl.hp_at(11) == 900

    def test_death_sets_zero(self):
        data = _data([{"t": 10, "type": "hit_taken", "damage": 100}, {"t": 30, "type": "death"}])
        assert hp_timeline_from_events(data, REC, 500).hp_at(31) == 0

    def test_none_without_max(self):
        assert hp_timeline_from_events(_data([{"t": 10, "type": "hit_taken", "damage": 1}]), REC, None) is None

    def test_real_v8_record_matches_battle_result(self):
        # T29 / Karelia の戦闘結果: maxHealth 1480、残り 391
        data = json.loads(V8.read_text(encoding="utf-8"))
        tl = hp_timeline_from_events(data, data["events"][0]["epoch"], None)
        assert tl.max_hp == 1480
        assert tl.hp_at(10_000) == 391


class TestFilters:
    def test_constant_hp(self):
        tl = HpTimeline(1000, [])
        f = build_hp_bar_filters(tl, 0.0, 8.0, DST)
        assert "text='1,000 / 1,000'" in f
        assert f"color={COLOR_HIGH}" in f
        assert COLOR_DRAIN not in f
        assert "drawbox=x=103:y=300:w=874:h=56" in f   # 中央寄せの背景

    def test_hit_inside_clip_splits_and_drains(self):
        tl = HpTimeline(1000, [(105.0, 400.0)])
        f = build_hp_bar_filters(tl, 103.0, 6.0, DST)
        assert "text='1,000 / 1,000':" in f and "between(t,0.0,2.0)" in f
        assert "text='400 / 1,000':" in f and "between(t,2.0,6.0)" in f
        assert COLOR_DRAIN in f                       # 削られた分が遅れて減る
        assert f"color={COLOR_MID}" in f              # 40% は黄

    def test_hit_before_clip_only_sets_start_value(self):
        tl = HpTimeline(1000, [(50.0, 200.0)])
        f = build_hp_bar_filters(tl, 103.0, 6.0, DST)
        assert "text='200 / 1,000'" in f and COLOR_DRAIN not in f
        assert f"color={COLOR_LOW}" in f              # 20% は赤

    def test_zero_hp_draws_no_fill(self):
        f = build_hp_bar_filters(HpTimeline(1000, [(1.0, 0.0)]), 2.0, 3.0, DST)
        assert "text='0 / 1,000'" in f
        assert COLOR_HIGH not in f and COLOR_MID not in f and COLOR_LOW not in f


def test_build_filter_args_uses_rendered_bar_instead_of_hud():
    from src.edit_video import build_filter_args
    hud = {"enabled": True, "src": {"x": 3, "y": 873, "w": 224, "h": 18}, "dst": DST}
    args = build_filter_args(1920, 1080, hud, None, hp_bar="drawbox=x=1")
    assert args[0] == "-vf" and args[1].endswith(",drawbox=x=1")
    assert "crop=224:18" not in args[1]
