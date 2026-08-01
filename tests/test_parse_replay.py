"""
parse_replay モジュールのテスト。
tests/fixtures/ 下のサンプルリプレイを使って期待値を固定する。
"""

import pytest
from pathlib import Path

# テスト用リプレイ（解析済みの既知ファイル）
REPLAY_FILE = Path(__file__).parent / "fixtures" / "20260604_1729_china-Ch20_Type58_115_sweden.wotreplay"

from src.parse_replay import (
    BattleInfo,
    PlayerStats,
    generate_title,
    parse_replay,
    read_replay_version,
)


@pytest.fixture(scope="module")
def info():
    return parse_replay(REPLAY_FILE)


@pytest.fixture(scope="module")
def stats(info):
    return info.player_stats


# ---- ファイル読み込み ----

class TestFileLoading:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_replay(Path("nonexistent.wotreplay"))

    def test_returns_battle_info(self):
        info = parse_replay(REPLAY_FILE)
        assert isinstance(info, BattleInfo)


# ---- 軽量バージョン読み取り ----

class TestReadReplayVersion:
    def test_known_version(self):
        assert read_replay_version(REPLAY_FILE) == "2.3.0.0"

    def test_missing_file_returns_empty(self):
        assert read_replay_version(Path("nonexistent.wotreplay")) == ""


# ---- バトル基本情報 ----

class TestBattleMetadata:
    def test_player_name(self, info):
        assert info.player_name == "PrsimPrsim"

    def test_vehicle(self, info):
        # Block1 の playerVehicle フィールドは "-" 区切り（ファイル名と同形式）
        assert info.player_vehicle == "china-Ch20_Type58"

    def test_map_name(self, info):
        assert info.map_name == "氷河"

    def test_game_version(self, info):
        assert info.game_version == "2.3.0.0"

    def test_region(self, info):
        assert info.region == "ASIA"

    def test_battle_datetime(self, info):
        import datetime
        assert info.battle_time == datetime.datetime(2026, 6, 4, 17, 29, 1)

    def test_is_random_battle(self, info):
        # このフィクスチャは実際の battleType=1（ランダム戦）
        assert info.battle_type == 1
        assert info.is_random_battle is True


# ---- バトル結果 ----

class TestBattleResult:
    def test_duration_seconds(self, info):
        assert info.duration_seconds == 365

    def test_winner_team(self, info):
        assert info.winner_team == 1

    def test_player_won(self, info):
        assert info.player_won is True


# ---- プレイヤー個人成績 ----

class TestPlayerStats:
    def test_returns_player_stats(self, stats):
        assert isinstance(stats, PlayerStats)

    def test_kills(self, stats):
        assert stats.kills == 1

    def test_damage_dealt(self, stats):
        assert stats.damage_dealt == 974

    def test_shots_fired(self, stats):
        assert stats.shots == 23

    def test_direct_hits(self, stats):
        assert stats.direct_hits == 13

    def test_survived(self, stats):
        assert stats.survived is True

    def test_hp_remaining(self, stats):
        assert stats.hp_remaining == 513

    def test_spotted(self, stats):
        assert stats.spotted == 3

    def test_radio_assist(self, stats):
        assert stats.damage_assisted_radio == 1031

    def test_xp(self, stats):
        assert stats.xp == 2530

    def test_credits(self, stats):
        assert stats.credits == 31124

    def test_mark_of_mastery(self, stats):
        assert stats.mark_of_mastery == 2

    def test_hit_rate(self, stats):
        # 13/23 ≈ 56.5%
        assert abs(stats.hit_rate - 13 / 23) < 0.001


# ---- タイトル生成 ----

class TestGenerateTitle:
    KOLOBANOV = 55  # medalKolobanov の DB ID
    TOP_GUN = 34    # warrior

    def _info(self, achievements: list[int], kills: int = 2) -> BattleInfo:
        import datetime
        return BattleInfo(
            player_name="PrsimPrsim",
            player_vehicle="usa-A40_T95",
            map_name="砂の川",
            game_version="2.3.0.0",
            region="ASIA",
            battle_time=datetime.datetime(2026, 7, 7, 23, 18, 0),
            duration_seconds=400,
            winner_team=1,
            player_team=1,
            player_stats=PlayerStats(
                kills=kills, damage_dealt=2416, shots=10, direct_hits=8,
                survived=True, hp_remaining=100, spotted=1,
                damage_assisted_radio=0, xp=900, credits=20000,
                mark_of_mastery=2, achievements=achievements,
            ),
        )

    def test_simple_format(self):
        # 「タンク名, キル数, ダメージ数」（2026-07-17 方針）
        assert generate_title(self._info([])) == "T95, 2キル, 2,416ダメージ"

    def test_medal_appended(self):
        title = generate_title(self._info([self.KOLOBANOV]))
        assert title == "T95, 2キル, 2,416ダメージ, コロバノフ勲章"

    def test_multiple_medals_epic_first(self):
        title = generate_title(self._info([self.TOP_GUN, self.KOLOBANOV]))
        assert title == "T95, 2キル, 2,416ダメージ, コロバノフ勲章, トップガン"

    def test_zero_kills_omitted(self):
        # 0キルはアピールにならないので項目ごと省く（2026-07-20 方針）
        assert generate_title(self._info([], kills=0)) == "T95, 2,416ダメージ"

    def test_zero_kills_with_medal(self):
        title = generate_title(self._info([self.KOLOBANOV], kills=0))
        assert title == "T95, 2,416ダメージ, コロバノフ勲章"

    def test_fixture_replay(self, info):
        # "Type58"(crude derivation) ではなく公式表示名 "Type 58"（vehicle_names.json 由来）
        assert generate_title(info) == "Type 58, 1キル, 974ダメージ"

    def test_unknown_vehicle_tag_falls_back_to_crude_derivation(self):
        # vehicle_names.json に無いタグ（新規車両等）は文字列加工にフォールバック
        info = self._info([])
        info.player_vehicle = "usa-A999_Brand_New_Tank"
        assert generate_title(info) == "Brand New Tank, 2キル, 2,416ダメージ"


# ---- 全車両リスト ----

class TestAllVehicles:
    def test_total_vehicles(self, info):
        assert len(info.all_vehicles) == 30

    def test_team_sizes(self, info):
        team1 = [v for v in info.all_vehicles if v["team"] == 1]
        team2 = [v for v in info.all_vehicles if v["team"] == 2]
        assert len(team1) == 15
        assert len(team2) == 15

    def test_player_vehicle_in_list(self, info):
        names = [v["name"] for v in info.all_vehicles]
        assert "PrsimPrsim" in names
