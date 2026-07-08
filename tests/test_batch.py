"""batch モジュールのロジック（録画価値フィルタ・ヘッダ判定）のテスト。"""

import shutil
import struct
from pathlib import Path

from src.batch import _has_result, _select_worthy, meets_criteria
from src.parse_replay import PlayerStats

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / \
    "20260604_1729_china-Ch20_Type58_115_sweden.wotreplay"


def _stats(kills=0, damage=0, mastery=0) -> PlayerStats:
    return PlayerStats(
        kills=kills, damage_dealt=damage, shots=10, direct_hits=5,
        survived=True, hp_remaining=100, spotted=0,
        damage_assisted_radio=0, xp=500, credits=10000,
        mark_of_mastery=mastery,
    )


class TestMeetsCriteria:
    def test_low_stats_rejected(self):
        assert not meets_criteria(_stats(kills=1, damage=800, mastery=1))

    def test_zero_stats_rejected(self):
        assert not meets_criteria(_stats())

    def test_kills_qualify(self):
        assert meets_criteria(_stats(kills=2))

    def test_damage_qualifies(self):
        assert meets_criteria(_stats(damage=1500))

    def test_mastery_qualifies(self):
        assert meets_criteria(_stats(mastery=3))

    def test_or_semantics(self):
        # どれか1つ満たせばよい
        assert meets_criteria(_stats(kills=0, damage=0, mastery=4))

    def test_custom_thresholds(self):
        s = _stats(kills=2, damage=1600, mastery=0)
        assert not meets_criteria(s, min_kills=3, min_damage=2000, min_mastery=4)
        assert meets_criteria(s, min_kills=2, min_damage=2000, min_mastery=4)


class TestHasResult:
    def test_two_blocks_header(self, tmp_path):
        p = tmp_path / "a.wotreplay"
        p.write_bytes(b"\x12\x32\x34\x11" + struct.pack("<I", 2) + b"rest")
        assert _has_result(p)

    def test_one_block_header(self, tmp_path):
        p = tmp_path / "b.wotreplay"
        p.write_bytes(b"\x12\x32\x34\x11" + struct.pack("<I", 1) + b"rest")
        assert not _has_result(p)

    def test_truncated_file(self, tmp_path):
        p = tmp_path / "c.wotreplay"
        p.write_bytes(b"\x12\x32")
        assert not _has_result(p)

    def test_real_fixture(self):
        assert _has_result(FIXTURE_REPLAY)


class TestSelectWorthy:
    """フィクスチャリプレイの成績: 1kill / 974dmg / M章2級。"""

    def test_below_default_thresholds_excluded(self, tmp_path):
        replay = tmp_path / FIXTURE_REPLAY.name
        shutil.copy(FIXTURE_REPLAY, replay)
        assert _select_worthy([replay], min_kills=2, min_damage=1500, min_mastery=3) == []

    def test_lower_thresholds_included(self, tmp_path):
        replay = tmp_path / FIXTURE_REPLAY.name
        shutil.copy(FIXTURE_REPLAY, replay)
        assert _select_worthy([replay], min_kills=1, min_damage=1500, min_mastery=3) == [replay]

    def test_unparseable_excluded(self, tmp_path):
        broken = tmp_path / "broken.wotreplay"
        broken.write_bytes(b"\x12\x32\x34\x11" + struct.pack("<I", 2) + b"garbage")
        assert _select_worthy([broken], min_kills=1, min_damage=1, min_mastery=1) == []
