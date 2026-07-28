"""src.longform の純粋ロジックのテスト。"""

import json
from pathlib import Path

from src.longform import (
    build_longform_description,
    build_longform_title,
    build_shorts_backfill_description,
    collect_pending_longform,
    is_longform_uploaded,
    mark_backfilled,
    mark_longform_uploaded,
    pending_backfills,
)


def _raw(dir_: Path, stem: str, with_meta: bool = True) -> Path:
    p = dir_ / f"{stem}.mp4"
    p.write_bytes(b"fake raw recording")
    if with_meta:
        p.with_suffix(".meta.json").write_text("{}", encoding="utf-8")
    return p


def _mark_shorts_uploaded(dir_: Path, stem: str, upload_log: Path, video_id_log: Path,
                          video_id: str | None = "vid123") -> None:
    shorts_stem = f"{stem}_shorts"
    upload_log.write_text(json.dumps([shorts_stem]), encoding="utf-8")
    if video_id is not None:
        video_id_log.write_text(json.dumps({shorts_stem: video_id}), encoding="utf-8")


class TestCollectPendingLongform:
    def test_empty_dir(self, tmp_path):
        assert collect_pending_longform(
            tmp_path, tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        ) == []

    def test_shorts_file_excluded(self, tmp_path):
        (tmp_path / "video_20260101_000000_shorts.mp4").write_bytes(b"x")
        assert collect_pending_longform(
            tmp_path, tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        ) == []

    def test_tmp_file_excluded(self, tmp_path):
        (tmp_path / "video_20260101_000000.tmp.mp4").write_bytes(b"x")
        assert collect_pending_longform(
            tmp_path, tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        ) == []

    def test_missing_meta_json_excluded(self, tmp_path):
        _raw(tmp_path, "video_20260101_000000", with_meta=False)
        up, vid, lf = tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        _mark_shorts_uploaded(tmp_path, "video_20260101_000000", up, vid)
        assert collect_pending_longform(tmp_path, up, vid, lf) == []

    def test_shorts_not_uploaded_excluded(self, tmp_path):
        _raw(tmp_path, "video_20260101_000000")
        up, vid, lf = tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        assert collect_pending_longform(tmp_path, up, vid, lf) == []

    def test_shorts_uploaded_but_no_video_id_excluded(self, tmp_path):
        _raw(tmp_path, "video_20260101_000000")
        up, vid, lf = tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        _mark_shorts_uploaded(tmp_path, "video_20260101_000000", up, vid, video_id=None)
        assert collect_pending_longform(tmp_path, up, vid, lf) == []

    def test_already_longform_uploaded_excluded(self, tmp_path):
        stem = "video_20260101_000000"
        raw = _raw(tmp_path, stem)
        up, vid, lf = tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        _mark_shorts_uploaded(tmp_path, stem, up, vid)
        mark_longform_uploaded(stem, "lfid", f"{stem}_shorts", lf)
        assert collect_pending_longform(tmp_path, up, vid, lf) == []

    def test_eligible_recording_included(self, tmp_path):
        stem = "video_20260101_000000"
        raw = _raw(tmp_path, stem)
        up, vid, lf = tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        _mark_shorts_uploaded(tmp_path, stem, up, vid)
        assert collect_pending_longform(tmp_path, up, vid, lf) == [raw]

    def test_sorted_by_filename(self, tmp_path):
        up, vid, lf = tmp_path / "up.json", tmp_path / "vid.json", tmp_path / "lf.json"
        _raw(tmp_path, "b_20260101_000000")
        _raw(tmp_path, "a_20260101_000000")
        up.write_text(json.dumps(["a_20260101_000000_shorts", "b_20260101_000000_shorts"]),
                       encoding="utf-8")
        vid.write_text(json.dumps({
            "a_20260101_000000_shorts": "id_a",
            "b_20260101_000000_shorts": "id_b",
        }), encoding="utf-8")
        result = collect_pending_longform(tmp_path, up, vid, lf)
        assert [p.stem for p in result] == ["a_20260101_000000", "b_20260101_000000"]


class TestBuildLongformTitle:
    def test_strips_shorts_hashtag(self):
        title = build_longform_title("T30, 1,866ダメージ #Shorts #WorldOfTanks")
        assert "#Shorts" not in title
        assert "#WorldOfTanks" in title

    def test_default_prefix(self):
        title = build_longform_title("T30, 1,866ダメージ")
        assert title == "【ノーカット】T30, 1,866ダメージ"

    def test_custom_prefix(self):
        title = build_longform_title("T30", prefix="[FULL] ")
        assert title == "[FULL] T30"


class TestBuildDescriptions:
    def test_longform_description_contains_url(self):
        desc = build_longform_description("https://youtu.be/abc123")
        assert "https://youtu.be/abc123" in desc

    def test_shorts_backfill_description_contains_url(self):
        desc = build_shorts_backfill_description("https://youtu.be/xyz789")
        assert "https://youtu.be/xyz789" in desc


class TestLongformLog:
    def test_is_longform_uploaded_false_when_missing(self, tmp_path):
        log = tmp_path / "lf.json"
        assert not is_longform_uploaded("stem", log)

    def test_mark_and_check(self, tmp_path):
        log = tmp_path / "lf.json"
        mark_longform_uploaded("stem", "vid1", "stem_shorts", log)
        assert is_longform_uploaded("stem", log)

    def test_mark_initializes_backfilled_false(self, tmp_path):
        log = tmp_path / "lf.json"
        mark_longform_uploaded("stem", "vid1", "stem_shorts", log)
        entries = json.loads(log.read_text(encoding="utf-8"))
        assert entries["stem"]["backfilled"] is False

    def test_mark_backfilled(self, tmp_path):
        log = tmp_path / "lf.json"
        mark_longform_uploaded("stem", "vid1", "stem_shorts", log)
        mark_backfilled("stem", log)
        entries = json.loads(log.read_text(encoding="utf-8"))
        assert entries["stem"]["backfilled"] is True

    def test_pending_backfills_excludes_done(self, tmp_path):
        log = tmp_path / "lf.json"
        mark_longform_uploaded("done", "vid1", "done_shorts", log)
        mark_backfilled("done", log)
        mark_longform_uploaded("pending", "vid2", "pending_shorts", log)
        pending = pending_backfills(log)
        assert [p["raw_stem"] for p in pending] == ["pending"]

    def test_pending_backfills_empty_when_log_missing(self, tmp_path):
        assert pending_backfills(tmp_path / "missing.json") == []
