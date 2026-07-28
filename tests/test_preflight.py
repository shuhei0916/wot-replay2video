"""preflight の純粋ロジック（ディスク容量チェック）のテスト。"""

from collections import namedtuple

import src.preflight as preflight

_Usage = namedtuple("Usage", ["total", "used", "free"])


class TestCheckDiskSpace:
    def test_enough_free_space_returns_no_problems(self, monkeypatch):
        monkeypatch.setattr(preflight, "MIN_FREE_GB", 50)
        monkeypatch.setattr(
            preflight.shutil, "disk_usage", lambda _: _Usage(0, 0, 100 * 1_000_000_000)
        )
        assert preflight.check_disk_space() == []

    def test_low_free_space_returns_problem(self, monkeypatch):
        monkeypatch.setattr(preflight, "MIN_FREE_GB", 50)
        monkeypatch.setattr(
            preflight.shutil, "disk_usage", lambda _: _Usage(0, 0, 10 * 1_000_000_000)
        )
        problems = preflight.check_disk_space()
        assert len(problems) == 1
        assert "10.0 GB" in problems[0]
        assert "50 GB" in problems[0]
