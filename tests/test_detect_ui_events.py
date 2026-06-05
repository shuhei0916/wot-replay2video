"""
UI イベント検出のテスト（アプローチ A）。

WoT バトル画面の固定 UI 領域（キル通知・ダメージログ・スコア表示）の
フレーム差分でイベントを検出する。

テスト用フィクスチャ:
  tests/fixtures/ui/quiet_a.jpg   : 変化なし（t=259.0s 付近）
  tests/fixtures/ui/quiet_b.jpg   : 変化なし（t=259.2s 付近）
  tests/fixtures/ui/event_before.jpg : イベント直前（t=261.5s）
  tests/fixtures/ui/event_after.jpg  : イベント直後（t=261.7s）
"""

import pytest
import numpy as np
from pathlib import Path

import cv2

FIXTURES = Path(__file__).parent / "fixtures" / "ui"
QUIET_A = FIXTURES / "quiet_a.jpg"
QUIET_B = FIXTURES / "quiet_b.jpg"
EVENT_BEFORE = FIXTURES / "event_before.jpg"
EVENT_AFTER = FIXTURES / "event_after.jpg"

REAL_VIDEO = Path(__file__).parent.parent / "output" / \
    "20260604_1729_china-Ch20_Type58_115_sweden_20260604_222206.mp4"

from src.detect_highlights import detect_highlights, HighlightEvent
from src.detect_ui_events import (
    compute_ui_diff,
    UIDiffResult,
    detect_ui_highlights,
    UI_ZONES,
)


# ---- UIDiffResult の構造 ----

class TestUIDiffResult:
    def test_has_kill_banner_score(self):
        r = compute_ui_diff(QUIET_A, QUIET_B)
        assert hasattr(r, "kill_banner")
        assert isinstance(r.kill_banner, float)

    def test_has_dmg_log_score(self):
        r = compute_ui_diff(QUIET_A, QUIET_B)
        assert hasattr(r, "dmg_log")
        assert isinstance(r.dmg_log, float)

    def test_has_score_top_score(self):
        r = compute_ui_diff(QUIET_A, QUIET_B)
        assert hasattr(r, "score_top")
        assert isinstance(r.score_top, float)

    def test_has_combined_score(self):
        r = compute_ui_diff(QUIET_A, QUIET_B)
        assert hasattr(r, "combined")
        assert isinstance(r.combined, float)


# ---- フレーム差分の値 ----

class TestFrameDiff:
    def test_quiet_frames_low_diff(self):
        r = compute_ui_diff(QUIET_A, QUIET_B)
        # 静かなフレーム間は全ゾーンで差分が小さいはず
        assert r.kill_banner < 15.0, f"kill_banner={r.kill_banner:.1f}"
        assert r.dmg_log < 15.0, f"dmg_log={r.dmg_log:.1f}"

    def test_event_frames_high_diff(self):
        r = compute_ui_diff(EVENT_BEFORE, EVENT_AFTER)
        # キル通知出現フレームは大きく変化する
        assert r.combined > 20.0, f"combined={r.combined:.1f}"

    def test_event_kill_banner_large(self):
        r = compute_ui_diff(EVENT_BEFORE, EVENT_AFTER)
        assert r.kill_banner > 20.0, f"kill_banner={r.kill_banner:.1f}"

    def test_event_larger_than_quiet(self):
        quiet = compute_ui_diff(QUIET_A, QUIET_B)
        event = compute_ui_diff(EVENT_BEFORE, EVENT_AFTER)
        assert event.combined > quiet.combined * 3


# ---- ゾーン定義 ----

class TestUIZones:
    def test_zones_defined(self):
        assert "kill_banner" in UI_ZONES
        assert "dmg_log" in UI_ZONES
        assert "score_top" in UI_ZONES

    def test_zone_format(self):
        # 各ゾーンは (y1, y2, x1, x2) のタプル
        for name, zone in UI_ZONES.items():
            assert len(zone) == 4, f"{name} のゾーン定義が不正"
            y1, y2, x1, x2 = zone
            assert y1 < y2
            assert x1 < x2


# ---- 動画全体での検出 ----

class TestDetectUIHighlights:
    @pytest.fixture(scope="class")
    def events(self):
        if not REAL_VIDEO.exists():
            pytest.skip("録画ファイルが存在しないためスキップ")
        return detect_ui_highlights(REAL_VIDEO)

    def test_returns_list(self, events):
        assert isinstance(events, list)

    def test_each_event_is_highlight(self, events):
        for e in events:
            assert isinstance(e, HighlightEvent)

    def test_finds_events(self, events):
        assert len(events) >= 1

    def test_detects_event_near_261s(self, events):
        # t=261.6s 付近の大きなイベントを検出できること
        timestamps = [e.timestamp for e in events]
        assert any(259.0 <= t <= 264.0 for t in timestamps), \
            f"261s 付近のイベントが見つからない: {timestamps}"

    def test_event_type(self, events):
        types = {e.event_type for e in events}
        assert "ui_change" in types

    def test_events_sorted(self, events):
        ts = [e.timestamp for e in events]
        assert ts == sorted(ts)
