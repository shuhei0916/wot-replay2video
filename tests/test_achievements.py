"""
achievements の勲章 ID → 日本語名解決のテスト。

src/data/achievements.json はコミット済みの静的データなので、
代表的な安定 DB ID（コロバノフ勲章=55 等）を直接検証できる。
"""

from src.achievements import notable_medals

KOLOBANOV = 55       # medalKolobanov（エピック）
TOP_GUN = 34         # warrior（バトルヒーロー）
SHOOT_TO_KILL = 521  # shootToKill（タイトルに載せない一般実績）


class TestNotableMedals:
    def test_empty(self):
        assert notable_medals([]) == []

    def test_epic_medal_resolved_to_japanese(self):
        assert notable_medals([KOLOBANOV]) == ["コロバノフ勲章"]

    def test_battle_hero_medal(self):
        assert notable_medals([TOP_GUN]) == ["トップガン"]

    def test_epic_comes_before_hero(self):
        assert notable_medals([TOP_GUN, KOLOBANOV]) == ["コロバノフ勲章", "トップガン"]

    def test_common_achievements_ignored(self):
        assert notable_medals([SHOOT_TO_KILL]) == []

    def test_unknown_id_ignored(self):
        assert notable_medals([999999]) == []


class TestEnglishNames:
    def test_english_epic_then_hero(self):
        assert notable_medals([TOP_GUN, KOLOBANOV], lang="en") == ["Kolobanov's Medal", "Top Gun"]

    def test_every_title_medal_has_english_name(self):
        from src.achievements import BATTLE_HERO_MEDALS, EN_NAMES, EPIC_MEDALS
        assert (EPIC_MEDALS | BATTLE_HERO_MEDALS) <= set(EN_NAMES)
