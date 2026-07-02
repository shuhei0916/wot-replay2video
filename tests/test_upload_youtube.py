"""
upload_youtube モジュールの純粋ロジック部分のテスト。
YouTube API 通信・OAuth フローはテスト対象外。
"""

import json
import pytest
from pathlib import Path

from src.upload_youtube import (
    extract_tags_from_title,
    build_video_metadata,
    is_uploaded,
    mark_as_uploaded,
    should_retry,
)


# ---- extract_tags_from_title ----

class TestExtractTagsFromTitle:
    def test_extracts_hashtags(self):
        title = "【WoT】O I 100 / 5kill / 3,800DMG / エル・ハルフ / 勝利 #Shorts #WorldOfTanks"
        tags = extract_tags_from_title(title)
        assert "Shorts" in tags
        assert "WorldOfTanks" in tags

    def test_returns_empty_when_no_hashtags(self):
        title = "【WoT】T34 1 / 0kill / 1,267DMG"
        assert extract_tags_from_title(title) == []

    def test_does_not_include_hash_symbol(self):
        tags = extract_tags_from_title("動画 #Shorts")
        assert all(not t.startswith("#") for t in tags)

    def test_multiple_tags(self):
        tags = extract_tags_from_title("title #A #B #C")
        assert tags == ["A", "B", "C"]


# ---- build_video_metadata ----

class TestBuildVideoMetadata:
    def test_title_is_set(self):
        meta = build_video_metadata("テストタイトル", privacy="private")
        assert meta["snippet"]["title"] == "テストタイトル"

    def test_privacy_status(self):
        meta = build_video_metadata("t", privacy="unlisted")
        assert meta["status"]["privacyStatus"] == "unlisted"

    def test_default_category_is_gaming(self):
        meta = build_video_metadata("t", privacy="private")
        assert meta["snippet"]["categoryId"] == "20"

    def test_custom_category(self):
        meta = build_video_metadata("t", privacy="private", category_id="22")
        assert meta["snippet"]["categoryId"] == "22"

    def test_hashtags_become_tags(self):
        meta = build_video_metadata("title #Shorts #WorldOfTanks", privacy="private")
        assert "Shorts" in meta["snippet"]["tags"]
        assert "WorldOfTanks" in meta["snippet"]["tags"]

    def test_extra_tags_are_merged(self):
        meta = build_video_metadata("title", privacy="private", extra_tags=["WoT", "戦車"])
        assert "WoT" in meta["snippet"]["tags"]
        assert "戦車" in meta["snippet"]["tags"]

    def test_tags_deduplicated(self):
        meta = build_video_metadata("title #WoT", privacy="private", extra_tags=["WoT"])
        assert meta["snippet"]["tags"].count("WoT") == 1


# ---- is_uploaded / mark_as_uploaded ----

class TestUploadLog:
    def test_is_uploaded_false_when_log_missing(self, tmp_path):
        log = tmp_path / "upload_log.json"
        assert not is_uploaded("video_stem", log)

    def test_is_uploaded_false_when_not_in_log(self, tmp_path):
        log = tmp_path / "upload_log.json"
        log.write_text(json.dumps(["other_video"]), encoding="utf-8")
        assert not is_uploaded("video_stem", log)

    def test_is_uploaded_true_when_in_log(self, tmp_path):
        log = tmp_path / "upload_log.json"
        log.write_text(json.dumps(["video_stem"]), encoding="utf-8")
        assert is_uploaded("video_stem", log)

    def test_mark_as_uploaded_creates_log(self, tmp_path):
        log = tmp_path / "upload_log.json"
        mark_as_uploaded("new_video", log)
        assert log.exists()
        assert "new_video" in json.loads(log.read_text(encoding="utf-8"))

    def test_mark_as_uploaded_appends(self, tmp_path):
        log = tmp_path / "upload_log.json"
        mark_as_uploaded("video_a", log)
        mark_as_uploaded("video_b", log)
        entries = json.loads(log.read_text(encoding="utf-8"))
        assert "video_a" in entries
        assert "video_b" in entries

    def test_mark_as_uploaded_no_duplicates(self, tmp_path):
        log = tmp_path / "upload_log.json"
        mark_as_uploaded("video_a", log)
        mark_as_uploaded("video_a", log)
        entries = json.loads(log.read_text(encoding="utf-8"))
        assert entries.count("video_a") == 1


# ---- should_retry ----

class TestShouldRetry:
    def test_retries_on_503(self):
        assert should_retry(status_code=503, attempt=1, max_attempts=3)

    def test_retries_on_500(self):
        assert should_retry(status_code=500, attempt=1, max_attempts=3)

    def test_retries_on_502(self):
        assert should_retry(status_code=502, attempt=1, max_attempts=3)

    def test_no_retry_on_403(self):
        assert not should_retry(status_code=403, attempt=1, max_attempts=3)

    def test_no_retry_on_400(self):
        assert not should_retry(status_code=400, attempt=1, max_attempts=3)

    def test_no_retry_when_max_reached(self):
        assert not should_retry(status_code=503, attempt=3, max_attempts=3)

    def test_retries_when_below_max(self):
        assert should_retry(status_code=503, attempt=2, max_attempts=3)
