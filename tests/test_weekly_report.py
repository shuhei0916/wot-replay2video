"""src/weekly_report.py の純粋ロジックのテスト。"""

import datetime

from src.weekly_report import (
    DayStats,
    format_report,
    open_todos_from_markdown,
    scheduled_stock,
    subscriber_history,
    weekly_buckets,
)

D = datetime.date
UTC = datetime.timezone.utc


def _day(d, views=10, gained=0, lost=0):
    return DayStats(d, views, views * 0.5, 80.0, gained, lost)


class TestSubscriberHistory:
    def test_back_calculates_from_current(self):
        days = [_day(D(2026, 11, 1), gained=2), _day(D(2026, 11, 2), gained=3, lost=1)]
        h = subscriber_history(20, days, D(2026, 11, 2))
        assert h[D(2026, 11, 2)] == 20
        assert h[D(2026, 11, 1)] == 18   # 11/2 は +2
        assert D(2026, 10, 31) not in h

    def test_days_after_data_are_flat(self):
        days = [_day(D(2026, 11, 1), gained=5)]
        h = subscriber_history(10, days, D(2026, 11, 3))
        assert h[D(2026, 11, 3)] == h[D(2026, 11, 2)] == h[D(2026, 11, 1)] == 10


class TestWeeklyBuckets:
    def test_splits_into_7_day_windows_oldest_first(self):
        end = D(2026, 11, 14)
        days = [_day(end - datetime.timedelta(days=i)) for i in range(14)]
        b = weekly_buckets(days, end, weeks=2)
        assert [(s, e) for s, e, _ in b] == [(D(2026, 11, 1), D(2026, 11, 7)), (D(2026, 11, 8), D(2026, 11, 14))]
        assert all(len(ds) == 7 for _, _, ds in b)


class TestScheduledStock:
    def test_counts_future_publish_at(self):
        now = datetime.datetime(2026, 11, 13, tzinfo=UTC)
        videos = [
            {"status": {"publishAt": "2026-11-20T10:00:00Z"}},
            {"status": {"publishAt": "2026-11-15T03:30:00Z"}},
            {"status": {"publishAt": "2026-11-01T03:30:00Z"}},  # 公開済み
            {"status": {}},
        ]
        n, last = scheduled_stock(videos, now)
        assert n == 2
        assert last == datetime.datetime(2026, 11, 20, 10, tzinfo=UTC)

    def test_empty(self):
        assert scheduled_stock([], datetime.datetime(2026, 11, 13, tzinfo=UTC)) == (0, None)


class TestOpenTodos:
    def test_extracts_unchecked_bold_titles_in_section(self):
        md = (
            "## 次にやる\n"
            "- [ ] **WN8 フィルタ**（説明）\n"
            "      続き\n"
            "- [x] **終わったもの**\n"
            "- [ ] 太字なし項目\n"
            "## 改善\n"
            "- [ ] **別セクション**\n"
        )
        assert open_todos_from_markdown(md) == ["WN8 フィルタ", "太字なし項目"]


class TestFormatReport:
    def _report(self, stock_until):
        end = D(2026, 11, 10)
        days = [_day(end - datetime.timedelta(days=i), views=100, gained=1) for i in range(60)]
        return format_report(
            channel_title="teach-me-wot",
            now=datetime.datetime(2026, 11, 13, 0, tzinfo=UTC),
            current_subs=11, total_views=5000, video_count=269,
            days=days, data_through=end,
            published_this_week=[{}] * 21,
            stock_count=9, stock_until=stock_until,
            top_videos=[("A-44 | 4 kills | 2,216 damage", 321, 85.0)],
            commits=["11/12 Add minimap overlays"],
            open_todos=["WN8 フィルタ"],
        )

    def test_contains_key_sections(self):
        text = self._report(datetime.datetime(2026, 11, 25, 10, tzinfo=UTC))
        assert "teach-me-wot 週次レポート（2026-11-13）" in text
        assert "登録者: 11 人" in text
        assert "今週公開された動画: 21 本" in text
        assert "あと約 12 日分" in text
        assert "⚠" not in text
        assert "A-44 | 4 kills" in text
        assert "Add minimap overlays" in text

    def test_warns_when_stock_is_low(self):
        text = self._report(datetime.datetime(2026, 11, 15, 10, tzinfo=UTC))
        assert "ストックが1週間を切っています" in text

    def test_warns_when_no_stock(self):
        text = self._report(None)
        assert "予約ストック: 0 本" in text
