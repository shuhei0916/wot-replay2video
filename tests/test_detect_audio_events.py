"""
detect_audio_events の純粋ロジックのテスト。
ffmpeg を使う extract_audio / detect_audio_events は対象外。
"""

import numpy as np

from src.detect_audio_events import (
    WINDOW_SEC,
    find_audio_peaks,
    fuse_events,
    rms_envelope,
)
from src.detect_highlights import HighlightEvent


def _env_with_peaks(duration_sec: float, peak_times: list[float],
                    floor_db: float = -50.0, peak_db: float = -10.0) -> np.ndarray:
    """ノイズフロア floor_db、指定位置に peak_db のスパイクを持つエンベロープ。"""
    n = int(duration_sec / WINDOW_SEC)
    env = np.full(n, floor_db)
    for t in peak_times:
        env[int(t / WINDOW_SEC)] = peak_db
    return env


def _event(t: float, score: float = 0.5, kind: str = "shot_flash") -> HighlightEvent:
    return HighlightEvent(timestamp=t, event_type=kind, score=score)


# ---- rms_envelope ----

class TestRmsEnvelope:
    def test_empty(self):
        assert len(rms_envelope(np.array([], dtype=np.float32), 16000)) == 0

    def test_silence_is_low_db(self):
        env = rms_envelope(np.zeros(16000, dtype=np.float32), 16000)
        assert np.all(env <= -100.0)

    def test_loud_signal_is_high_db(self):
        sine = np.sin(np.linspace(0, 2000, 16000)).astype(np.float32)
        env = rms_envelope(sine, 16000)
        assert np.all(env > -6.0)


# ---- find_audio_peaks ----

class TestFindAudioPeaks:
    def test_empty(self):
        assert find_audio_peaks(np.array([])) == []

    def test_flat_envelope_no_peaks(self):
        env = np.full(1000, -50.0)
        assert find_audio_peaks(env) == []

    def test_detects_single_peak(self):
        env = _env_with_peaks(30.0, [10.0])
        peaks = find_audio_peaks(env)
        assert len(peaks) == 1
        t, score = peaks[0]
        assert abs(t - 10.0) < 0.2
        assert score == 1.0  # フロアから40dB超 → 上限クリップ

    def test_detects_multiple_peaks(self):
        env = _env_with_peaks(60.0, [5.0, 20.0, 45.0])
        peaks = find_audio_peaks(env)
        assert len(peaks) == 3

    def test_close_peaks_merged(self):
        # min_gap_sec=1.5 より近い2つのピークは1イベントに統合
        env = _env_with_peaks(30.0, [10.0, 10.5])
        peaks = find_audio_peaks(env)
        assert len(peaks) == 1

    def test_small_bump_below_threshold_ignored(self):
        env = np.full(600, -50.0)
        env[100] = -45.0  # フロア+5dB では閾値(+12dB)未満
        assert find_audio_peaks(env) == []


# ---- fuse_events ----

class TestFuseEvents:
    def test_empty(self):
        assert fuse_events([], []) == []

    def test_audio_only_kept(self):
        audio = [_event(10.0, 0.6, "shot_audio")]
        fused = fuse_events([], audio)
        assert len(fused) == 1
        assert fused[0].event_type == "shot_audio"
        assert fused[0].score == 0.6

    def test_matched_pair_boosted(self):
        flash = [_event(10.1, 0.5)]
        audio = [_event(10.0, 0.6, "shot_audio")]
        fused = fuse_events(flash, audio)
        assert len(fused) == 1
        assert fused[0].event_type == "shot_confirmed"
        assert fused[0].score == 0.9  # 0.6 + 0.3

    def test_unmatched_flash_penalized(self):
        flash = [_event(30.0, 0.8)]
        audio = [_event(10.0, 0.6, "shot_audio")]
        fused = fuse_events(flash, audio)
        assert len(fused) == 2
        flash_out = [e for e in fused if e.event_type == "shot_flash"][0]
        assert flash_out.score == 0.4  # 0.8 * 0.5

    def test_result_sorted(self):
        flash = [_event(30.0, 0.8)]
        audio = [_event(50.0, 0.6, "shot_audio"), _event(10.0, 0.6, "shot_audio")]
        fused = fuse_events(flash, audio)
        ts = [e.timestamp for e in fused]
        assert ts == sorted(ts)

    def test_score_capped_at_1(self):
        flash = [_event(10.0, 0.5)]
        audio = [_event(10.0, 0.9, "shot_audio")]
        fused = fuse_events(flash, audio)
        assert fused[0].score == 1.0
