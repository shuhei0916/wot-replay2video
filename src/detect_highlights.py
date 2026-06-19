"""
録画動画からハイライトシーン（射撃フラッシュ）を検出する。

検出手法:
- shot_flash: 画面中央 ROI の輝度スパイク（銃口フラッシュ・着弾フラッシュ）

WoT では射撃・着弾時に照準付近（画面中央）が一瞬明るくなる。
全画面ではなく中央 ROI に絞ることで UI 変化・ローディング等の誤検出を抑制する。
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class HighlightEvent:
    timestamp: float   # 動画内の秒数
    event_type: str    # "shot_flash" など
    score: float       # 0.0〜1.0 （強度の正規化値）


# 射撃フラッシュ検出用 ROI（元解像度に対する比率）
# 照準は画面中央にあるため、中央 50% の領域を監視する
_ROI_X1, _ROI_X2 = 0.25, 0.75  # 横: 25%〜75%
_ROI_Y1, _ROI_Y2 = 0.25, 0.75  # 縦: 25%〜75%


def _center_brightness(frame: np.ndarray) -> float:
    """フレームの中央 ROI のグレースケール平均輝度を返す。"""
    h, w = frame.shape[:2]
    y1, y2 = int(h * _ROI_Y1), int(h * _ROI_Y2)
    x1, x2 = int(w * _ROI_X1), int(w * _ROI_X2)
    roi = frame[y1:y2, x1:x2]
    # 処理負荷削減のため 1/4 縮小
    small = cv2.resize(roi, (0, 0), fx=0.25, fy=0.25)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return float(gray.mean())


def detect_highlights(
    video_path: Path,
    flash_threshold: float = 30.0,
    min_brightness: float = 40.0,
    cooldown_sec: float = 2.0,
) -> list[HighlightEvent]:
    """
    動画を解析して射撃フラッシュイベントのリストを返す。

    画面中央 ROI の輝度スパイクを検出するため、UI 変化や
    画面端の明るさ変化には反応しない。

    Args:
        video_path: 解析対象の動画
        flash_threshold: ROI のフレーム間輝度差がこの値を超えたら射撃とみなす
        min_brightness: ローディング画面除外用の ROI 最低平均輝度
        cooldown_sec: 同種イベントを連続検出しないクールダウン秒数

    Returns:
        タイムスタンプ順に並んだ HighlightEvent のリスト
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"動画を開けません: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    events: list[HighlightEvent] = []
    prev_brightness: float | None = None
    last_flash_time: float = -cooldown_sec

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            timestamp = frame_idx / fps

            brightness = _center_brightness(frame)

            # ローディング画面（ROI が暗すぎる）はスキップ
            if brightness < min_brightness:
                prev_brightness = brightness
                continue

            if prev_brightness is not None:
                delta = brightness - prev_brightness

                if delta >= flash_threshold:
                    if timestamp - last_flash_time >= cooldown_sec:
                        score = min(delta / (flash_threshold * 3), 1.0)
                        events.append(HighlightEvent(
                            timestamp=round(timestamp, 2),
                            event_type="shot_flash",
                            score=round(score, 3),
                        ))
                        last_flash_time = timestamp

            prev_brightness = brightness

    finally:
        cap.release()

    return sorted(events, key=lambda e: e.timestamp)
