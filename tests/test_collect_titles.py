"""src.collect_titles（人気 Shorts タイトル収集）の通信以外の部分のテスト。"""

import datetime

from src.collect_titles import (
    is_due,
    is_popular_short,
    load_ledger,
    merge_records,
    parse_iso_duration,
    save_ledger,
    top_titles,
    video_record,
    views_per_day,
)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 23, 3, 0, tzinfo=UTC)


def _item(vid="v1", title="T", views=50000, duration="PT45S", channel="c1",
          published="2026-11-13T03:00:00Z"):
    return {
        "id": vid,
        "snippet": {"title": title, "channelId": channel, "channelTitle": channel.upper(),
                    "publishedAt": published},
        "contentDetails": {"duration": duration},
        "statistics": {"viewCount": str(views), "likeCount": "10"},
    }


class TestParseIsoDuration:
    def test_variants(self):
        assert parse_iso_duration("PT45S") == 45
        assert parse_iso_duration("PT1M5S") == 65
        assert parse_iso_duration("PT2M") == 120
        assert parse_iso_duration("PT1H0M1S") == 3601
        assert parse_iso_duration("P0D") == 0

    def test_invalid(self):
        assert parse_iso_duration("") is None
        assert parse_iso_duration("PT") is None
        assert parse_iso_duration("45s") is None


class TestVideoRecord:
    def test_fields(self):
        rec = video_record(_item(), "WoT shorts", NOW)
        assert rec["video_id"] == "v1"
        assert rec["duration_sec"] == 45
        assert rec["views"] == 50000
        assert rec["comments"] == 0  # 非公開のとき欠ける
        assert rec["queries"] == ["WoT shorts"]
        assert rec["view_history"] == [["2026-11-23", 50000]]

    def test_missing_duration(self):
        assert video_record(_item(duration=""), "q", NOW) is None


class TestIsPopularShort:
    def test_filters(self):
        ok = video_record(_item(), "q", NOW)
        assert is_popular_short(ok, 180, 10000)
        assert not is_popular_short(video_record(_item(duration="PT4M"), "q", NOW), 180, 10000)
        assert not is_popular_short(video_record(_item(duration="P0D"), "q", NOW), 180, 10000)  # 配信中など
        assert not is_popular_short(video_record(_item(views=9999), "q", NOW), 180, 10000)
        assert not is_popular_short(ok, 180, 10000, own_channel_id="c1")


class TestMergeRecords:
    def test_add_then_update(self):
        ledger = {}
        assert merge_records(ledger, [video_record(_item(), "a", NOW)]) == (1, 0)
        later = NOW + datetime.timedelta(days=1)
        rec = video_record(_item(title="T2", views=80000), "b", later)
        assert merge_records(ledger, [rec]) == (0, 1)
        r = ledger["v1"]
        assert r["title"] == "T2"
        assert r["views"] == 80000
        assert r["queries"] == ["a", "b"]
        assert r["first_seen"] == NOW.isoformat(timespec="seconds")
        assert r["view_history"] == [["2026-11-23", 50000], ["2026-11-24", 80000]]

    def test_same_day_overwrites_history(self):
        ledger = {}
        merge_records(ledger, [video_record(_item(), "a", NOW)])
        merge_records(ledger, [video_record(_item(views=60000), "a", NOW)])
        assert ledger["v1"]["view_history"] == [["2026-11-23", 60000]]
        assert ledger["v1"]["queries"] == ["a"]


class TestRanking:
    def test_views_per_day(self):
        rec = video_record(_item(views=100000), "q", NOW)  # 公開 10 日前
        assert views_per_day(rec, NOW) == 10000

    def test_minimum_one_day(self):
        rec = video_record(_item(views=5000, published="2026-11-23T02:00:00Z"), "q", NOW)
        assert views_per_day(rec, NOW) == 5000

    def test_top_titles_caps_per_channel(self):
        ledger = {}
        recs = [video_record(_item(vid=f"a{i}", views=100000 + i, channel="big"), "q", NOW) for i in range(5)]
        recs.append(video_record(_item(vid="b", views=20000, channel="small"), "q", NOW))
        merge_records(ledger, recs)
        top = top_titles(ledger, 10, NOW, per_channel=2)
        assert [r["video_id"] for r in top] == ["a4", "a3", "b"]


class TestIsDue:
    def test_due(self):
        assert is_due({}, 20, NOW)
        assert not is_due({"last_run": (NOW - datetime.timedelta(hours=5)).isoformat()}, 20, NOW)
        assert is_due({"last_run": (NOW - datetime.timedelta(hours=21)).isoformat()}, 20, NOW)
        assert is_due({"last_run": "garbage"}, 20, NOW)


class TestLedgerIO:
    def test_roundtrip_and_missing(self, tmp_path):
        p = tmp_path / "popular_titles.json"
        assert load_ledger(p) == {"meta": {}, "videos": {}}
        data = {"meta": {"last_run": "x"}, "videos": {"v": {"title": "戦車"}}}
        save_ledger(data, p)
        assert load_ledger(p) == data

    def test_corrupt(self, tmp_path):
        p = tmp_path / "popular_titles.json"
        p.write_text("{broken", encoding="utf-8")
        assert load_ledger(p) == {"meta": {}, "videos": {}}
