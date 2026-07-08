"""
llm_title の純粋ロジックのテスト。
claude CLI を呼ぶ generate_title_llm は対象外（プロンプト構築と出力整形のみ）。
"""

import datetime

from src.llm_title import (
    MAX_TITLE_LEN,
    build_multilang_prompt,
    build_prompt,
    clean_title,
    parse_multilang_titles,
)
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

    def test_requests_marker_format(self):
        prompt = build_prompt(_info())
        assert "<title>" in prompt


class TestCleanTitle:
    def test_marker_extraction(self):
        assert clean_title("<title>弾あたらん😡</title>") == "弾あたらん😡"

    def test_marker_wins_over_preamble(self):
        # 前置きがあってもマーカー内だけを採用する
        raw = "ダメージを基にタイトルを生成します。\n<title>今日は当たる日</title>\n以上です。"
        assert clean_title(raw) == "今日は当たる日"

    def test_no_marker_first_line_fallback(self):
        assert clean_title("O-I 100 は正義\n補足説明") == "O-I 100 は正義"

    def test_meta_preamble_without_marker_rejected(self):
        # 過去の事故: 前置き文がそのままタイトルになった
        assert clean_title("ダメージを基にタイトルを生成します") is None
        assert clean_title("以下のタイトル案はいかがでしょう") is None

    def test_empty_returns_none(self):
        assert clean_title("") is None
        assert clean_title("   \n  ") is None
        assert clean_title("<title></title>") is None

    def test_strips_surrounding_quotes(self):
        assert clean_title("<title>「弾あたらん…」</title>") == "弾あたらん…"

    def test_too_long_returns_none(self):
        assert clean_title("あ" * (MAX_TITLE_LEN + 1)) is None
        assert clean_title(f"<title>{'あ' * (MAX_TITLE_LEN + 1)}</title>") is None

    def test_multiline_inside_marker_rejected(self):
        assert clean_title("<title>一行目\n二行目</title>") is None


class TestMultilangPrompt:
    def test_requests_three_languages(self):
        prompt = build_multilang_prompt(_info())
        assert "<ja>" in prompt
        assert "<en>" in prompt
        assert "<ru>" in prompt


class TestParseMultilangTitles:
    def test_all_three(self):
        raw = "<ja>弾あたらん😡</ja>\n<en>Can't hit anything 😡</en>\n<ru>Не попадаю 😡</ru>"
        titles = parse_multilang_titles(raw)
        assert titles == {
            "ja": "弾あたらん😡",
            "en": "Can't hit anything 😡",
            "ru": "Не попадаю 😡",
        }

    def test_ja_only_ok(self):
        assert parse_multilang_titles("<ja>今日は当たる日</ja>") == {"ja": "今日は当たる日"}

    def test_missing_ja_returns_none(self):
        assert parse_multilang_titles("<en>English only</en>") is None

    def test_preamble_ignored(self):
        raw = "以下のタイトルを生成しました。\n<ja>3キル劇</ja>\n<en>Triple kill</en>"
        titles = parse_multilang_titles(raw)
        assert titles["ja"] == "3キル劇"
        assert titles["en"] == "Triple kill"

    def test_empty_returns_none(self):
        assert parse_multilang_titles("") is None
        assert parse_multilang_titles("<ja></ja>") is None

    def test_too_long_lang_dropped(self):
        raw = f"<ja>OK</ja>\n<en>{'x' * (MAX_TITLE_LEN + 1)}</en>"
        titles = parse_multilang_titles(raw)
        assert titles == {"ja": "OK"}
