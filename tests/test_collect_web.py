"""src.collect_web（Web リプレイ収集）の通信以外の部分のテスト。"""

import json
from pathlib import Path

from src.collect_web import (
    Candidate,
    build_description,
    interleave,
    load_ledger,
    origin_for,
    parse_skill4ltu_listing,
    parse_wot_record_listing,
    prefilter,
    replay_filename,
    save_ledger,
    skill4ltu_url,
    verify,
    wot_record_url,
)
from src.parse_replay import parse_replay

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / \
    "20260604_1729_china-Ch20_Type58_115_sweden.wotreplay"

WR_HTML = (
    '<a class="tovabb" href="replay/447801/some_title"><div class="wrapper">'
    '<img class="level" src="style/images/wot/levels/tank_level_8.png" alt=""/>'
    '<div class="upload-info"><span class="date-cont">2026-11-13 07:34:03</span> | unamakiCAN</div>'
    '<div class="cell"><img class="icon_view" src="x.png" alt=""/><span>198</span></div>'
    '<div class="cell"><img style="margin-top: -2px;" src="c.png" alt="WN8"/>'
    '<span style="font-weight:bold;color:#722EB5;">8 700</span></div>'
    '<div class="cell"><img class="icon_kills" src="k.png" alt=""/><span>8</span></div>'
    '<div class="cell"><img class="icon_damage" src="d.png" alt=""/><span>5 853</span></div>'
    '</div></a>'
)


def _cand(source="wot_record", id_="1", player="A", damage=5000, kills=3, wn8=5000.0):
    return Candidate(source, id_, f"https://example.invalid/{id_}", "", player, damage, kills, wn8)


class TestListingParsers:
    def test_wot_record(self):
        [c] = parse_wot_record_listing(WR_HTML)
        assert (c.id, c.player, c.damage, c.kills, c.wn8) == ("447801", "unamakiCAN", 5853, 8, 8700.0)
        assert c.download_url == "https://wot-record.com/download/447801"
        assert c.page_url == "https://wot-record.com/replay/447801/some_title"

    def test_skill4ltu(self):
        payload = {"items": [
            {"id": "69c879e5-80b0-4070-8316-ea6821f63aaf", "player_name": "Kavis",
             "vehicle_tank_id": "1", "damage_dealt": 3385, "kills": 3, "outcome": "victory"},
            {"id": "../evil", "player_name": "x"},
        ]}
        [c] = parse_skill4ltu_listing(payload)
        assert c.player == "Kavis" and c.damage == 3385 and c.wn8 > 0
        assert c.download_url.endswith("/api/replays/69c879e5-80b0-4070-8316-ea6821f63aaf/download")

    def test_urls(self):
        assert wot_record_url(2, "2.4.1.0", [8, 9]) == (
            "https://wot-record.com/replays/date/desc?page=2&version=2.4"
            "&game=ctf,domination,assault&tier=8,9"
        )
        u = skill4ltu_url(1, "2.4.1.0", [8, 9])
        assert "version=2.4.1&" in u and "tier=8,9" in u and "mode=random" in u


class TestPrefilter:
    def test_threshold_ledger_and_order(self):
        cands = [_cand(id_="1", wn8=3000), _cand(id_="2", player="B", wn8=9000),
                 _cand(id_="3", player="C", wn8=6000), _cand(id_="4", player="D", wn8=None)]
        got = prefilter(cands, {"wot_record:3": {}}, min_wn8=4000)
        assert [c.id for c in got] == ["2"]

    def test_same_battle_on_two_sites_kept_once(self):
        cands = [_cand(source="skill4ltu", id_="x", wn8=7000), _cand(id_="1", wn8=8000)]
        got = prefilter(cands, {}, min_wn8=4000)
        assert [c.key for c in got] == ["wot_record:1"]


class TestVerify:
    def test_accept_and_reject(self):
        info = parse_replay(FIXTURE_REPLAY)  # v2.3.0.0, WN8 約 2315
        assert verify(info, "2.3.0.0", 2000) is None
        assert "version" in verify(info, "2.4.1.0", 2000)
        assert "wn8" in verify(info, "2.3.0.0", 4000)
        info.battle_type = 22
        assert "battleType" in verify(info, "2.3.0.0", 2000)

    def test_filename_is_sanitized(self):
        info = parse_replay(FIXTURE_REPLAY)
        info.player_vehicle = "china-Ch20/../Type58 x"
        name = replay_filename(info, _cand(source="skill4ltu", id_="69c879e5-80b0"))
        assert name == "20260604_1729_china-Ch20Type58x_web-skill4ltu-69c879e5.wotreplay"


class TestLedgerAndCredit:
    def test_origin_and_description(self, tmp_path):
        save_ledger({"wot_record:1": {"file": "a.wotreplay", "page_url": "https://wot-record.com/replay/1/t"},
                     "wot_record:2": {"file": None, "status": "rejected"}}, tmp_path)
        assert json.loads((tmp_path / "collected.json").read_text(encoding="utf-8")) == load_ledger(tmp_path)
        assert origin_for("own.wotreplay", tmp_path) is None
        origin = origin_for("a.wotreplay", tmp_path)
        desc = build_description(parse_replay(FIXTURE_REPLAY), origin)
        assert desc == "Player: PrsimPrsim (ASIA)\nReplay: https://wot-record.com/replay/1/t"


class TestInterleave:
    def test_ratio(self):
        assert interleave([1, 2, 3, 4, 5], ["a", "b", "c"], 2) == [1, 2, "a", 3, 4, "b", 5, "c"]

    def test_only_one_side(self):
        assert interleave([], ["a", "b"], 2) == ["a", "b"]
        assert interleave([1, 2, 3], [], 2) == [1, 2, 3]
