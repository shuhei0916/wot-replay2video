"""src.heat（見どころ曲線・ヒート制）のテスト。"""

import json
from pathlib import Path

import numpy as np

from src.heat import (
    DRAMA_KILL,
    DRAMA_NO_DAMAGE_HIT,
    HeatInput,
    XP_PER_DAMAGE,
    XP_PER_DAMAGING_HIT,
    XP_PER_KILL,
    build_curve,
    heat_inputs,
    hot_segments,
    plot_curve,
    select_segments,
    xp_reference,
)

REC = 1000.0
FIXTURE = Path(__file__).parent / "fixtures" / "mod_v7_chigo_ensk.events.json"


def _data(events):
    return {"events": [dict(e, epoch=REC + e["t"]) for e in events]}


class TestHeatInputs:
    def test_none_without_v7(self):
        assert heat_inputs(_data([{"t": 10, "type": "shot"}]), REC) is None

    def test_xp_amounts(self):
        data = _data([
            {"t": 10, "type": "hit_dealt", "damage_factor": 50},
            {"t": 10, "type": "damage_dealt", "damage": 300, "killed": False},
            {"t": 20, "type": "hit_dealt", "damage_factor": 0},
            {"t": 30, "type": "damage_dealt", "damage": 150, "killed": True},
            {"t": 40, "type": "hit_taken", "damage": 200},
            {"t": -5, "type": "hit_dealt", "damage_factor": 0},   # 録画開始前は無視
        ])
        got = [(h.timestamp, h.kind, round(h.xp, 2)) for h in heat_inputs(data, REC)]
        assert got == [
            (10.0, "hit", XP_PER_DAMAGING_HIT),
            (10.0, "damage", 300 * XP_PER_DAMAGE),
            (20.0, "no_damage_hit", DRAMA_NO_DAMAGE_HIT),
            (30.0, "damage", 150 * XP_PER_DAMAGE),
            (30.0, "kill", XP_PER_KILL + DRAMA_KILL),
            (40.0, "taken", 10.0),
        ]


class TestCurve:
    def test_jump_and_half_life(self):
        c = build_curve([HeatInput(10.0, 100.0, "damage")], 60.0, xp_ref=100.0, half_life=5.0)
        assert c.at(9.9) == 0.0
        assert abs(c.at(10.0) - 1.0) < 1e-9
        assert abs(c.at(15.0) - 0.5) < 1e-6
        assert abs(c.at(20.0) - 0.25) < 1e-6

    def test_accumulates(self):
        one = build_curve([HeatInput(10.0, 50.0, "damage")], 30.0, 100.0)
        two = build_curve([HeatInput(10.0, 50.0, "damage"), HeatInput(11.0, 50.0, "damage")], 30.0, 100.0)
        assert two.at(11.0) > one.at(10.0)

    def test_normalized_by_reference(self):
        lo = build_curve([HeatInput(10.0, 50.0, "damage")], 30.0, xp_ref=100.0)
        hi = build_curve([HeatInput(10.0, 50.0, "damage")], 30.0, xp_ref=400.0)
        assert lo.at(10.0) == 4 * hi.at(10.0)

    def test_xp_reference(self):
        assert xp_reference(None) == 200.0
        assert xp_reference(999_999_999) == 200.0   # 期待値表に無い車両
        assert xp_reference(1) > 0                  # 期待値表にある車両


class TestSegments:
    def _curve(self, times, xp=100.0, dur=120.0):
        return build_curve([HeatInput(t, xp, "damage") for t in times], dur, xp_ref=100.0)

    def test_single_event_segment_has_lead_and_tail(self):
        # 熱1.0 → 0.1 に冷めるのは半減期5秒で約16.6秒後
        segs = hot_segments(self._curve([30.0]), threshold=0.1, lead=2.5, tail=0.5, gap=1.0)
        assert len(segs) == 1
        a, b = segs[0]
        assert a == 27.5
        assert 46.5 <= b <= 47.5

    def test_close_events_merge_far_split(self):
        assert len(hot_segments(self._curve([30.0, 40.0]), threshold=0.1)) == 1
        assert len(hot_segments(self._curve([10.0, 90.0]), threshold=0.1)) == 2

    def test_nothing_hot(self):
        assert hot_segments(self._curve([]), threshold=0.1) == []

    def test_select_raises_threshold_to_fit_cap(self):
        c = self._curve([10.0, 40.0, 70.0, 100.0])
        segs, th = select_segments(c, max_total=20.0, min_total=0.0, threshold=0.1)
        assert sum(b - a for a, b in segs) <= 20.0
        assert th > 0.1

    def test_select_keeps_hottest(self):
        c = build_curve([HeatInput(10.0, 30.0, "damage"), HeatInput(60.0, 200.0, "kill")], 100.0, 100.0)
        segs, _ = select_segments(c, max_total=10.0, min_total=0.0, threshold=0.1)
        assert len(segs) == 1 and segs[0][0] <= 60.0 <= segs[0][1]

    def test_select_lowers_threshold_for_min_total(self):
        c = build_curve([HeatInput(10.0, 12.0, "damage")], 60.0, xp_ref=100.0)  # 0.12 の小さな山
        base = sum(b - a for a, b in hot_segments(c, 0.1))
        segs, th = select_segments(c, max_total=150.0, min_total=15.0, threshold=0.1, min_threshold=0.03)
        assert th < 0.1 and sum(b - a for a, b in segs) > base


class TestRealBattle:
    def test_fixture_curve(self, tmp_path):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        start = data["events"][0]["epoch"]
        c = build_curve(heat_inputs(data, start), 340.0, xp_ref=150.0)
        segs, th = select_segments(c, max_total=40.0)
        assert 0 < sum(b - a for a, b in segs) <= 40.0
        # 一番アツい瞬間（撃破付近）は必ず残る
        peak_t = float(np.argmax(c.values)) * c.dt
        assert any(a <= peak_t <= b for a, b in segs)
        out = plot_curve(c, segs, th, tmp_path / "heat.png")
        assert out.exists() and out.stat().st_size > 1000
