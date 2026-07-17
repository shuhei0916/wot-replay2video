"""
schedule（予約投稿のスロット割り当て）の純粋ロジックのテスト。
config / state ファイルを読む allocate_publish_at は対象外
（next_slot / parse_times / to_publish_at / commit_publish_at のみ）。
"""

import datetime

from src.schedule import JST, commit_publish_at, next_slot, parse_times, to_publish_at

TIMES = parse_times(["07:00", "12:30", "19:00"])


def _jst(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=JST)


class TestParseTimes:
    def test_sorted_and_deduped(self):
        times = parse_times(["19:00", "07:00", "19:00"])
        assert times == [datetime.time(7, 0), datetime.time(19, 0)]


class TestNextSlot:
    def test_first_allocation_uses_next_slot_of_today(self):
        # 朝9時 → 当日の 12:30
        slot = next_slot(None, _jst(2026, 7, 17, 9, 0), TIMES)
        assert slot == _jst(2026, 7, 17, 12, 30)

    def test_lead_time_excludes_imminent_slot(self):
        # 12:20 の実行では 12:30 は 15 分リードを満たさない → 19:00
        slot = next_slot(None, _jst(2026, 7, 17, 12, 20), TIMES)
        assert slot == _jst(2026, 7, 17, 19, 0)

    def test_after_last_slot_rolls_to_next_day(self):
        slot = next_slot(None, _jst(2026, 7, 17, 22, 0), TIMES)
        assert slot == _jst(2026, 7, 18, 7, 0)

    def test_fills_after_last_scheduled(self):
        # 最後尾が当日 12:30 → 次は 19:00
        slot = next_slot(_jst(2026, 7, 17, 12, 30), _jst(2026, 7, 17, 9, 0), TIMES)
        assert slot == _jst(2026, 7, 17, 19, 0)

    def test_last_scheduled_in_future_days(self):
        # 予約テールが2日先まで伸びている → その翌スロットに詰める
        slot = next_slot(_jst(2026, 7, 19, 19, 0), _jst(2026, 7, 17, 9, 0), TIMES)
        assert slot == _jst(2026, 7, 20, 7, 0)

    def test_stale_last_scheduled_self_heals(self):
        # state が過去に取り残されていても now 基準で復帰する
        slot = next_slot(_jst(2026, 7, 1, 19, 0), _jst(2026, 7, 17, 9, 0), TIMES)
        assert slot == _jst(2026, 7, 17, 12, 30)


class TestPublishAtRoundtrip:
    def test_to_publish_at_is_utc(self):
        # JST 19:00 → UTC 10:00
        assert to_publish_at(_jst(2026, 7, 17, 19, 0)) == "2026-07-17T10:00:00Z"

    def test_commit_and_reload(self, tmp_path):
        state = tmp_path / "schedule_state.json"
        commit_publish_at("2026-07-17T10:00:00Z", state_path=state)
        # 記録された最後尾の後に詰めることを、state 経由で確認
        from src.schedule import _load_last
        last = _load_last(state)
        assert last == _jst(2026, 7, 17, 19, 0)
        slot = next_slot(last, _jst(2026, 7, 17, 9, 0), TIMES)
        assert slot == _jst(2026, 7, 18, 7, 0)

    def test_load_last_missing_file(self, tmp_path):
        from src.schedule import _load_last
        assert _load_last(tmp_path / "nope.json") is None
