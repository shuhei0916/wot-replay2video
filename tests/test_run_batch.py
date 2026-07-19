"""run_batch.collect_replays（日付・バージョンフィルタ）のテスト。"""

import shutil
from pathlib import Path

from run_batch import MIN_DATE, collect_replays

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / \
    "20260604_1729_china-Ch20_Type58_115_sweden.wotreplay"


def _copy_as(dir_: Path, name: str) -> Path:
    dst = dir_ / name
    shutil.copy(FIXTURE_REPLAY, dst)
    return dst


class TestCollectReplays:
    def test_empty_dir(self, tmp_path):
        assert collect_replays(tmp_path) == []

    def test_old_dates_excluded(self, tmp_path):
        _copy_as(tmp_path, "20260101_0000_old.wotreplay")
        new = _copy_as(tmp_path, f"{MIN_DATE}_0000_new.wotreplay")
        assert collect_replays(tmp_path) == [new]

    def test_same_version_all_kept(self, tmp_path):
        # フィクスチャ由来なので全ファイル同一バージョン → 全部残る
        a = _copy_as(tmp_path, "20260620_0000_a.wotreplay")
        b = _copy_as(tmp_path, "20260621_0000_b.wotreplay")
        assert collect_replays(tmp_path) == [a, b]

    def test_sorted_by_name(self, tmp_path):
        b = _copy_as(tmp_path, "20260622_0000_b.wotreplay")
        a = _copy_as(tmp_path, "20260620_0000_a.wotreplay")
        assert collect_replays(tmp_path) == [a, b]

    def test_non_date_names_excluded(self, tmp_path):
        # ゲームが書き込み中の temp.wotreplay は候補にもバージョン判定にも使わない
        # （"temp" は辞書順で日付より後ろに来て「最新リプレイ」扱いになる事故があった）
        a = _copy_as(tmp_path, "20260620_0000_a.wotreplay")
        (tmp_path / "temp.wotreplay").write_bytes(b"\x00" * 16)
        assert collect_replays(tmp_path) == [a]
