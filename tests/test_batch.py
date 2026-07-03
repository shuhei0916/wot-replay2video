"""batch モジュールの純粋ロジック（録画価値フィルタ）のテスト。"""

from src.batch import meets_criteria
from src.parse_replay import PlayerStats


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
