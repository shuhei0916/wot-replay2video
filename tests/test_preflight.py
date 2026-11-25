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


class TestModDeployReason:
    def _setup(self, tmp_path, pyc_newer=True):
        import os
        mods, dest = tmp_path / "mods", tmp_path / "client"
        mods.mkdir(); dest.mkdir()
        for src, out in preflight.MOD_FILES.items():
            (mods / src).write_text("x")
            (dest / out).write_bytes(b"x")
            t = 1_000_000
            os.utime(mods / src, (t, t))
            os.utime(dest / out, (t + (10 if pyc_newer else -10),) * 2)
        return mods, dest

    def test_up_to_date(self, tmp_path):
        mods, dest = self._setup(tmp_path)
        assert preflight.mod_deploy_reason(dest, mods) is None

    def test_missing_pyc(self, tmp_path):
        # クライアント更新で res_mods のバージョンフォルダが変わったケース（2026-11-24）
        mods, dest = self._setup(tmp_path)
        (dest / "mod_shot_logger.pyc").unlink()
        assert "mod_shot_logger.pyc" in preflight.mod_deploy_reason(dest, mods)

    def test_missing_dest_dir(self, tmp_path):
        mods, _ = self._setup(tmp_path)
        assert preflight.mod_deploy_reason(tmp_path / "2.5.0.0", mods) is not None

    def test_source_newer(self, tmp_path):
        mods, dest = self._setup(tmp_path, pyc_newer=False)
        assert "新しい" in preflight.mod_deploy_reason(dest, mods)
