"""src.wn8（1戦 WN8）のテスト。"""

from pathlib import Path

import pytest

from src.parse_replay import parse_replay
from src.wn8 import battle_wn8

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / \
    "20260604_1729_china-Ch20_Type58_115_sweden.wotreplay"


class TestBattleWn8:
    def test_unknown_vehicle_is_none(self):
        assert battle_wn8(-1, 5000, 5) is None

    def test_zero_battle_is_zero(self):
        assert battle_wn8(1, 0, 0) == 0

    def test_more_damage_scores_higher(self):
        assert battle_wn8(1, 2000, 2) > battle_wn8(1, 1000, 2)

    def test_win_scores_higher(self):
        assert battle_wn8(1, 1000, 1, won=True) > battle_wn8(1, 1000, 1, won=False)

    def test_expected_performance_is_about_average(self):
        # 期待値どおりの成績（勝率も期待値）なら WN8 は約 1565 になる定義。
        # 1戦単位では勝敗が 0/100% なので、勝ち試合はそれより上に出る
        from src.wn8 import _expected
        e = _expected()[1]
        w = battle_wn8(1, e["expDamage"], e["expFrag"], e["expSpot"], e["expDef"], won=True)
        assert 1565 < w < 1900

    def test_replay_wn8(self):
        info = parse_replay(FIXTURE_REPLAY)
        assert info.player_stats.type_comp_descr == 5169
        assert info.wn8 == pytest.approx(2314.7, abs=0.1)
