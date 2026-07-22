"""
vehicle_names の車両タグ → 公式表示名解決のテスト。

src/data/vehicle_names.json はコミット済みの静的データなので、
代表的な安定タグを直接検証できる（WG の車両改名で内部タグと現行公式名が
食い違う例を含む）。
"""

from src.vehicle_names import official_vehicle_name


class TestOfficialVehicleName:
    def test_renamed_vehicle_returns_current_name(self):
        # 内部タグ 'Ch43_WZ_122_2' は現在「122 TM」として表示される
        assert official_vehicle_name("china-Ch43_WZ_122_2") == "122 TM"

    def test_slash_in_official_name_preserved(self):
        assert official_vehicle_name("sweden-S29_UDES_14_5") == "UDES 14 Alt 5"

    def test_simple_name(self):
        assert official_vehicle_name("usa-A40_T95") == "T95"

    def test_unknown_tag_returns_none(self):
        assert official_vehicle_name("usa-NOT_A_REAL_TAG") is None

    def test_empty_string_returns_none(self):
        assert official_vehicle_name("") is None
