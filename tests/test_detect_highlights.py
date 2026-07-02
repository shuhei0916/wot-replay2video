"""
detect_highlights モジュールのテスト。
合成動画（tests/fixtures/）と実際の録画で動作を検証する。

合成動画は数秒しかないため、実録画向けデフォルトの
skip_initial_sec（ローディング除外）を 0 にして呼び出す。
"""

import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
FLAT_VIDEO = FIXTURES / "flat.mp4"
FLASH_VIDEO = FIXTURES / "flash_at_1s.mp4"
REAL_VIDEO = Path(__file__).parent.parent / "output" / \
    "20260604_1729_china-Ch20_Type58_115_sweden_20260604_222206.mp4"

from src.detect_highlights import detect_highlights, HighlightEvent


def _detect(video_path: Path) -> list[HighlightEvent]:
    """合成フィクスチャ用: ローディングスキップなしで検出する。"""
    return detect_highlights(video_path, skip_initial_sec=0.0)


# ---- 基本動作 ----

class TestDetectHighlightsBasic:
    def test_returns_list(self):
        events = _detect(FLAT_VIDEO)
        assert isinstance(events, list)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            detect_highlights(Path("nonexistent.mp4"))

    def test_each_event_has_timestamp(self):
        events = _detect(FLASH_VIDEO)
        for e in events:
            assert isinstance(e, HighlightEvent)
            assert isinstance(e.timestamp, float)
            assert e.timestamp >= 0.0

    def test_each_event_has_type(self):
        events = _detect(FLASH_VIDEO)
        for e in events:
            assert isinstance(e.event_type, str)
            assert len(e.event_type) > 0

    def test_each_event_has_score(self):
        events = _detect(FLASH_VIDEO)
        for e in events:
            assert isinstance(e.score, float)
            assert 0.0 <= e.score <= 1.0


# ---- フラッシュ検出 ----

class TestFlashDetection:
    def test_flat_video_no_highlights(self):
        events = _detect(FLAT_VIDEO)
        assert len(events) == 0

    def test_flash_video_detects_event(self):
        events = _detect(FLASH_VIDEO)
        assert len(events) >= 1

    def test_flash_detected_near_1_second(self):
        events = _detect(FLASH_VIDEO)
        # フラッシュは 1.0 秒付近（± 0.5 秒の誤差を許容）
        timestamps = [e.timestamp for e in events]
        assert any(0.5 <= t <= 1.5 for t in timestamps), \
            f"1秒付近のイベントが見つからない: {timestamps}"

    def test_flash_event_type(self):
        events = _detect(FLASH_VIDEO)
        types = {e.event_type for e in events}
        assert "shot_flash" in types

    def test_skip_initial_excludes_early_events(self):
        # デフォルト相当の skip を渡すと 1 秒地点のフラッシュは検出されない
        events = detect_highlights(FLASH_VIDEO, skip_initial_sec=2.0)
        assert all(e.timestamp >= 2.0 for e in events)


# ---- 実録画での動作（smoke test） ----

@pytest.fixture(scope="module")
def real_events():
    if not REAL_VIDEO.exists():
        pytest.skip("録画ファイルが存在しないためスキップ")
    return detect_highlights(REAL_VIDEO)


class TestRealVideoSmoke:
    def test_finds_some_highlights(self, real_events):
        # 6分間の戦闘リプレイなので何らかのハイライトがあるはず
        assert len(real_events) >= 1

    def test_highlights_within_video_duration(self, real_events):
        # 動画は 6:51 = 411 秒
        for e in real_events:
            assert e.timestamp <= 420.0, f"動画外のタイムスタンプ: {e.timestamp}"

    def test_events_sorted_by_timestamp(self, real_events):
        timestamps = [e.timestamp for e in real_events]
        assert timestamps == sorted(timestamps)
