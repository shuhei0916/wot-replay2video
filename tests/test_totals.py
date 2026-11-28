"""src.totals（試合の累計表示）のテスト。"""

import json
from pathlib import Path

from src.totals import FLASH_COLOR, TEXT_COLOR, build_totals_filters, totals_timeline

REC = 1000.0
V10 = Path(__file__).parent / "fixtures" / "mod_v10_t29_karelia.events.json"
ICONS = {"damage": Path("d.png"), "blocked": Path("b.png"), "assist": Path("a.png")}


def _be(t, name, amount):
    return {"type": "battle_event", "name": name, "details": [amount, 0, 0], "epoch": REC + t}


class TestTimeline:
    def test_v10_battle_events(self):
        data = {"events": [_be(10, "DAMAGE", 300), _be(12, "TANKING", 220), _be(15, "RADIO_ASSIST", 100),
                           _be(20, "DAMAGE", 250), _be(21, "TRACK_ASSIST", 50), _be(22, "SPOTTED", 0)]}
        tl = totals_timeline(data, REC)
        assert tl["damage"] == [(10.0, 300), (20.0, 550)]
        assert tl["blocked"] == [(12.0, 220)]
        assert tl["assist"] == [(15.0, 100), (21.0, 150)]     # 偵察・履帯・スタンはまとめてアシスト

    def test_before_v10_uses_damage_dealt_only(self):
        data = {"events": [{"type": "damage_dealt", "damage": 300, "epoch": REC + 5},
                           {"type": "damage_dealt", "damage": 200, "epoch": REC + 9}]}
        tl = totals_timeline(data, REC)
        assert list(tl) == ["damage"] and tl["damage"] == [(5.0, 300), (9.0, 500)]

    def test_real_v10_record_matches_battle_result(self):
        data = json.loads(V10.read_text(encoding="utf-8"))
        tl = totals_timeline(data, min(e["epoch"] for e in data["events"]))
        assert tl["blocked"][-1][1] == 660 and tl["assist"][-1][1] == 116
        assert tl["damage"][-1][1] == 4605 - 338   # ブラインドショットの1発は戦果イベントにも来ない


class TestFilters:
    def test_value_changes_and_flashes(self):
        tl = {"damage": [(10.0, 300), (14.0, 550)], "blocked": [], "assist": []}
        f = build_totals_filters(tl, 8.0, 10.0, ICONS, Path("w.ttf"), 392)
        assert "text='0'" in f and "text='300'" in f and "text='550'" in f
        assert f"fontcolor={FLASH_COLOR}" in f and f"fontcolor={TEXT_COLOR}" in f
        assert "gte(t,2.0)*lt(t,2.5)" in f            # 増えた直後 0.5 秒は金色
        assert f.count("movie='") == 3                # アイコン3つ
        assert "between(t" not in f                   # 境目で前後の数字が重ならない

    def test_comma_formatting(self):
        tl = {"damage": [(1.0, 12345)]}
        f = build_totals_filters(tl, 0.0, 5.0, ICONS, Path("w.ttf"), 392)
        assert "text='12,345'" in f and f.count("movie='") == 1

    def test_empty(self):
        assert build_totals_filters({}, 0.0, 5.0, ICONS, Path("w.ttf"), 392) == ""
