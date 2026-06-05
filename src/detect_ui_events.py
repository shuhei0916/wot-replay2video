"""
WoT バトル画面の UI 固定領域を監視してハイライトイベントを検出する。

検出ゾーン（1920x1080 基準）:
  kill_banner : キル通知帯（右下、ミニマップ上）
  dmg_log     : ダメージログ（左パネル）
  score_top   : スコア表示（画面上部中央）
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from src.detect_highlights import HighlightEvent

# UI ゾーン定義: (y1, y2, x1, x2) ← 1920x1080 基準
UI_ZONES = {
    "kill_banner": (484, 504, 1140, 1432),
    "dmg_log":    (56,  146, 286,  440),
    "score_top":  (6,   30,  420,  660),
}

# combined スコアの重み
_WEIGHTS = {
    "kill_banner": 0.5,
    "dmg_log":     0.3,
    "score_top":   0.2,
}


@dataclass
class UIDiffResult:
    kill_banner: float
    dmg_log: float
    score_top: float

    @property
    def combined(self) -> float:
        return (
            self.kill_banner * _WEIGHTS["kill_banner"]
            + self.dmg_log   * _WEIGHTS["dmg_log"]
            + self.score_top * _WEIGHTS["score_top"]
        )


def _read_gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"画像を読み込めません: {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _zone_diff(gray_a: np.ndarray, gray_b: np.ndarray, zone: tuple) -> float:
    """指定ゾーンのフレーム間平均絶対差を返す。"""
    y1, y2, x1, x2 = zone
    h, w = gray_a.shape
    # 解像度に応じてスケール
    sy, sx = h / 1080, w / 1920
    ry1, ry2 = int(y1 * sy), int(y2 * sy)
    rx1, rx2 = int(x1 * sx), int(x2 * sx)
    roi_a = gray_a[ry1:ry2, rx1:rx2]
    roi_b = gray_b[ry1:ry2, rx1:rx2]
    return float(np.abs(roi_a - roi_b).mean())


def compute_ui_diff(frame_a: Path, frame_b: Path) -> UIDiffResult:
    """
    2 フレーム間の UI ゾーン差分を計算する。

    Args:
        frame_a: 前フレームの画像パス
        frame_b: 後フレームの画像パス

    Returns:
        UIDiffResult（各ゾーンの差分値と combined スコア）
    """
    gray_a = _read_gray(frame_a)
    gray_b = _read_gray(frame_b)

    return UIDiffResult(
        kill_banner=_zone_diff(gray_a, gray_b, UI_ZONES["kill_banner"]),
        dmg_log=_zone_diff(gray_a, gray_b, UI_ZONES["dmg_log"]),
        score_top=_zone_diff(gray_a, gray_b, UI_ZONES["score_top"]),
    )


def detect_ui_highlights(
    video_path: Path,
    threshold: float = 15.0,
    cooldown_sec: float = 2.0,
    min_brightness: float = 60.0,
) -> list[HighlightEvent]:
    """
    UI ゾーンの変化からハイライトイベントを検出する。

    Args:
        video_path: 解析対象の動画
        threshold: combined スコアがこの値を超えたらイベントとみなす
        cooldown_sec: 同種イベントの最小間隔（秒）
        min_brightness: ローディング画面除外のための最低輝度

    Returns:
        タイムスタンプ順の HighlightEvent リスト
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"動画が見つかりません: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"動画を開けません: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    events: list[HighlightEvent] = []
    last_event_time = -cooldown_sec

    prev_gray: np.ndarray | None = None

    # 処理負荷削減のため 4 フレームに 1 回だけ評価（≒7.5fps）
    STRIDE = 4

    try:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1

            if frame_idx % STRIDE != 0:
                continue

            timestamp = (frame_idx - 1) / fps

            small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)

            if gray.mean() < min_brightness:
                prev_gray = gray
                continue

            if prev_gray is not None:
                diffs = {
                    k: _zone_diff(prev_gray, gray, UI_ZONES[k])
                    for k in UI_ZONES
                }
                result = UIDiffResult(**diffs)

                if result.combined >= threshold:
                    if timestamp - last_event_time >= cooldown_sec:
                        score = min(result.combined / (threshold * 5), 1.0)
                        events.append(HighlightEvent(
                            timestamp=round(timestamp, 2),
                            event_type="ui_change",
                            score=round(score, 3),
                        ))
                        last_event_time = timestamp

            prev_gray = gray

    finally:
        cap.release()

    return sorted(events, key=lambda e: e.timestamp)
