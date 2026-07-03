"""launcher の純粋ロジック（ログマーカー検索）のテスト。"""

import datetime

from src.launcher import find_marker_since

MARKER = b"BattleLoadingSpace"


def _line(ts: str, text: str) -> bytes:
    return f"{ts}.123: INFO: Main: {text}\n".encode()


class TestFindMarkerSince:
    def test_empty_log(self):
        since = datetime.datetime(2026, 7, 3, 13, 0, 0)
        assert find_marker_since(b"", MARKER, since) is None

    def test_finds_marker_after_since(self):
        log = _line("2026-07-03 13:05:44", "Space is updated: BattleLoadingSpace()")
        since = datetime.datetime(2026, 7, 3, 13, 5, 0)
        pos = find_marker_since(log, MARKER, since)
        assert pos is not None
        assert log[:pos].endswith(MARKER)

    def test_ignores_stale_marker(self):
        # 過去セッションの残骸は無視する
        log = _line("2026-07-03 11:32:34", "Space is updated: BattleLoadingSpace()")
        since = datetime.datetime(2026, 7, 3, 13, 5, 0)
        assert find_marker_since(log, MARKER, since) is None

    def test_skips_stale_finds_fresh(self):
        log = (
            _line("2026-07-03 11:32:34", "Space is updated: BattleLoadingSpace()")
            + _line("2026-07-03 12:00:00", "some other line")
            + _line("2026-07-03 13:05:44", "Space is updated: BattleLoadingSpace()")
        )
        since = datetime.datetime(2026, 7, 3, 13, 5, 0)
        pos = find_marker_since(log, MARKER, since)
        assert pos is not None
        # 2番目（新しい方）のマーカー位置であること
        assert log[:pos].count(MARKER) == 2

    def test_truncated_log_with_fresh_marker(self):
        # ログが切り詰められて小さくなっていても、新しい行なら見つかる
        log = _line("2026-07-03 13:06:00", "Space is changed: BattleLoadingSpace() -> ReplayBattleSpace()")
        since = datetime.datetime(2026, 7, 3, 13, 5, 0)
        assert find_marker_since(log, MARKER, since) is not None

    def test_line_without_timestamp_ignored(self):
        log = b"no timestamp here BattleLoadingSpace\n"
        since = datetime.datetime(2026, 7, 3, 13, 5, 0)
        assert find_marker_since(log, MARKER, since) is None
