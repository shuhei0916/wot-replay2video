"""src.prune_replays（旧バージョンのリプレイ整理）のファイル操作以外のテスト。"""

import json
from pathlib import Path

from src.prune_replays import (
    append_archive,
    archive_record,
    archived_files,
    is_obsolete,
    pending_replay_stems,
    select_obsolete,
    version_key,
)

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / \
    "20260604_1729_china-Ch20_Type58_115_sweden.wotreplay"


class TestVersion:
    def test_version_key(self):
        assert version_key("2.4.2.0") == (2, 4, 2, 0)
        assert version_key("") is None
        assert version_key("2.x") is None

    def test_is_obsolete(self):
        assert is_obsolete("2.4.1.0", "2.4.2.0")
        assert is_obsolete("2.3.1.0", "2.4.0.0")
        assert is_obsolete("2.4.10.0", "2.4.9.0") is False  # 文字列比較ではなく数値比較
        assert not is_obsolete("2.4.2.0", "2.4.2.0")
        assert not is_obsolete("2.5.0.0", "2.4.2.0")  # 別 PC の新しいクライアント分は守る
        assert not is_obsolete("", "2.4.2.0")         # 不明は消さない


class TestSelectObsolete:
    def test_select(self):
        versions = {"old": "2.4.1.0", "cur": "2.4.2.0", "unk": "", "keep": "2.3.0.0"}
        paths = [Path(f"{k}.wotreplay") for k in versions]
        got = select_obsolete(paths, "2.4.2.0", {"keep"}, lambda p: versions[p.stem])
        assert got == [(Path("old.wotreplay"), "2.4.1.0")]


class TestPendingStems:
    def test_only_not_uploaded(self, tmp_path):
        (tmp_path / "a_20261124_215438_shorts.mp4").touch()
        (tmp_path / "b_20261124_215439_shorts.mp4").touch()
        (tmp_path / "c_20261124_215440.mp4").touch()  # フル録画は対象外
        log = tmp_path / "uploaded.json"
        log.write_text(json.dumps(["b_20261124_215439_shorts"]), encoding="utf-8")
        assert pending_replay_stems(tmp_path, log) == {"a"}


class TestArchive:
    def test_record_from_fixture(self):
        rec = archive_record(FIXTURE_REPLAY, "2.3.0.0")
        assert rec["file"] == FIXTURE_REPLAY.name
        assert rec["version"] == "2.3.0.0"
        assert rec["stats"]["damage_dealt"] == 974
        assert rec["stats"]["kills"] == 1
        json.dumps(rec)  # そのまま JSON にできる

    def test_record_unparsable(self, tmp_path):
        p = tmp_path / "broken.wotreplay"
        p.write_bytes(b"\x00" * 8)
        rec = archive_record(p, "2.4.0.0")
        assert rec["file"] == "broken.wotreplay"
        assert "error" in rec

    def test_append_and_read(self, tmp_path):
        a = tmp_path / "replay_archive.jsonl"
        assert archived_files(a) == set()
        append_archive([{"file": "x.wotreplay"}, {"file": "y.wotreplay"}], a)
        append_archive([{"file": "z.wotreplay"}], a)
        assert archived_files(a) == {"x.wotreplay", "y.wotreplay", "z.wotreplay"}
