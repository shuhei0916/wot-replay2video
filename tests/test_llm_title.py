"""
llm_title の純粋ロジックのテスト。
claude CLI を呼ぶ generate_title_llm は対象外（プロンプト構築と出力整形のみ）。
"""

import datetime

from src.llm_title import MAX_TITLE_LEN, build_prompt, clean_title
from src.parse_replay import BattleInfo, PlayerStats


def _info() -> BattleInfo:
    return BattleInfo(
        player_name="PrsimPrsim",
        player_vehicle="china-Ch20_Type58",
        map_name="氷河",
        game_version="2.3.0.0",
        region="ASIA",
        battle_time=datetime.datetime(2026, 6, 4, 17, 29, 1),
        duration_seconds=365,
        winner_team=1,
        player_team=1,
        player_stats=PlayerStats(
            kills=3, damage_dealt=1500, shots=10, direct_hits=7,
            survived=True, hp_remaining=200, spotted=2,
            damage_assisted_radio=500, xp=1200, credits=30000,
            mark_of_mastery=2,
        ),
    )


class TestBuildPrompt:
    def test_contains_battle_facts(self):
        prompt = build_prompt(_info())
        assert "Type58" in prompt
        assert "氷河" in prompt
        assert "勝利" in prompt
        assert "3" in prompt      # kills
        assert "1500" in prompt   # damage

    def test_contains_required_tags(self):
        prompt = build_prompt(_info())
        assert "#Shorts" in prompt
        assert "#WorldOfTanks" in prompt


class TestCleanTitle:
    def test_valid_title_passthrough(self):
        t = "Type 58が氷河で3キル大暴れ！ #Shorts #WorldOfTanks"
        assert clean_title(t) == t

    def test_empty_returns_none(self):
        assert clean_title("") is None
        assert clean_title("   \n  ") is None

    def test_takes_first_line(self):
        raw = "Type 58の3キル劇 #Shorts #WorldOfTanks\n\nこのタイトルは戦績を強調しています。"
        assert clean_title(raw) == "Type 58の3キル劇 #Shorts #WorldOfTanks"

    def test_strips_surrounding_quotes(self):
        assert clean_title('「Type 58が3キル #Shorts #WorldOfTanks」') == \
            "Type 58が3キル #Shorts #WorldOfTanks"

    def test_appends_missing_tags(self):
        cleaned = clean_title("Type 58が3キル")
        assert cleaned == "Type 58が3キル #Shorts #WorldOfTanks"

    def test_too_long_returns_none(self):
        assert clean_title("あ" * (MAX_TITLE_LEN + 1)) is None

    def test_too_long_after_tags_returns_none(self):
        base = "あ" * (MAX_TITLE_LEN - 5)  # タグ追記で上限超過
        assert clean_title(base) is None

    def test_tag_case_insensitive(self):
        t = "3キル #shorts #worldoftanks"
        assert clean_title(t) == t
