"""
録画の音声トラックから砲撃音などの大音量イベントを検出する。

WoT の砲撃音・被弾音・爆発音は録画音声の中で最も大きい過渡音なので、
RMS エンベロープのスパイク検出だけで頑健にハイライト候補が得られる。
輝度フラッシュ検出（detect_highlights）と融合して使う。
"""

import subprocess
from pathlib import Path

import numpy as np

from src.config import find_ffmpeg
from src.detect_highlights import HighlightEvent

SAMPLE_RATE = 16000       # 解析用サンプルレート（ピーク検出には十分）
WINDOW_SEC = 0.05         # RMS エンベロープの窓幅
PEAK_DB_ABOVE_FLOOR = 12.0  # ノイズフロア(中央値)からの超過 dB
MIN_GAP_SEC = 1.5         # 連続ピークの統合間隔
SCORE_FULL_DB = 30.0      # スコア 1.0 になるフロア超過 dB


def extract_audio(video_path: Path, rate: int = SAMPLE_RATE) -> np.ndarray:
    """動画の音声をモノラル float32 PCM として取り出す。"""
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg が見つかりません")
    r = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-vn",
         "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"],
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"音声を抽出できません: {video_path}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def rms_envelope(samples: np.ndarray, rate: int, window_sec: float = WINDOW_SEC) -> np.ndarray:
    """窓ごとの RMS(dBFS) エンベロープを返す。"""
    win = max(int(rate * window_sec), 1)
    n = len(samples) // win
    if n == 0:
        return np.array([])
    chunks = samples[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(chunks ** 2, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-8))


def find_audio_peaks(
    envelope_db: np.ndarray,
    window_sec: float = WINDOW_SEC,
    peak_db_above_floor: float = PEAK_DB_ABOVE_FLOOR,
    min_gap_sec: float = MIN_GAP_SEC,
) -> list[tuple[float, float]]:
    """
    エンベロープからピークを検出して (秒, スコア) のリストを返す。

    ノイズフロア（エンベロープの中央値）から peak_db_above_floor dB
    以上高い窓をピークとみなし、min_gap_sec 以内の連続ピークは
    最大値の位置に統合する。スコアはフロア超過 dB を 0-1 に正規化。
    """
    if len(envelope_db) == 0:
        return []

    floor = float(np.median(envelope_db))
    threshold = floor + peak_db_above_floor
    above = envelope_db >= threshold

    peaks: list[tuple[float, float]] = []
    i = 0
    n = len(envelope_db)
    while i < n:
        if not above[i]:
            i += 1
            continue
        # 連続領域 + min_gap 以内の再上昇を1つのイベントに統合
        j = i
        last_above = i
        while j < n and (j - last_above) * window_sec < min_gap_sec:
            if above[j]:
                last_above = j
            j += 1
        segment = envelope_db[i : last_above + 1]
        k = int(np.argmax(segment)) + i
        excess = float(envelope_db[k]) - floor
        score = min(excess / SCORE_FULL_DB, 1.0)
        peaks.append((round(k * window_sec, 2), round(score, 3)))
        i = j
    return peaks


def detect_audio_events(
    video_path: Path,
    skip_initial_sec: float = 0.0,
) -> list[HighlightEvent]:
    """
    動画の音声から大音量イベントを検出して HighlightEvent のリストを返す。

    Args:
        video_path: 解析対象の動画
        skip_initial_sec: 先頭から除外する秒数（ローディング音対策）
    """
    samples = extract_audio(Path(video_path))
    env = rms_envelope(samples, SAMPLE_RATE)
    events = [
        HighlightEvent(timestamp=t, event_type="shot_audio", score=s)
        for t, s in find_audio_peaks(env)
        if t >= skip_initial_sec
    ]
    return events


def fuse_events(
    flash_events: list[HighlightEvent],
    audio_events: list[HighlightEvent],
    match_window_sec: float = 0.5,
) -> list[HighlightEvent]:
    """
    輝度フラッシュと音声ピークを融合する。

    - 音声ピークを一次候補とする（砲撃音は輝度より高精度）
    - ±match_window_sec 以内にフラッシュがあるピークはスコアを加点
      （両方の証拠が揃った「確実な射撃」）
    - フラッシュ単独のイベントは減点して残す（音が拾えないケースの保険）
    """
    fused: list[HighlightEvent] = []
    flash_times = [e.timestamp for e in flash_events]

    matched_flash: set[int] = set()
    for a in audio_events:
        bonus = 0.0
        for i, ft in enumerate(flash_times):
            if abs(ft - a.timestamp) <= match_window_sec:
                matched_flash.add(i)
                bonus = 0.3
                break
        fused.append(HighlightEvent(
            timestamp=a.timestamp,
            event_type="shot_confirmed" if bonus else "shot_audio",
            score=round(min(a.score + bonus, 1.0), 3),
        ))

    for i, f in enumerate(flash_events):
        if i not in matched_flash:
            fused.append(HighlightEvent(
                timestamp=f.timestamp,
                event_type=f.event_type,
                score=round(f.score * 0.5, 3),
            ))

    return sorted(fused, key=lambda e: e.timestamp)
