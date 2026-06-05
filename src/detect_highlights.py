"""
録画動画からハイライトシーンを検出する。

検出手法:
- brightness_flash: フレーム間の輝度差スパイク（着弾・爆発時の閃光）
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class HighlightEvent:
    timestamp: float   # 動画内の秒数
    event_type: str    # "brightness_flash" など
    score: float       # 0.0〜1.0 （強度の正規化値）


def detect_highlights(
    video_path: Path,
    flash_threshold: float = 40.0,
    min_brightness: float = 60.0,
    cooldown_sec: float = 2.0,
) -> list[HighlightEvent]:
    """
    動画を解析してハイライトイベントのリストを返す。

    Args:
        video_path: 解析対象の動画（WSL パス）
        flash_threshold: フレーム間輝度差がこの値を超えたらフラッシュとみなす
        min_brightness: ローディング画面除外用の最低平均輝度
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
    last_flash_time: float = -cooldown_sec  # 最後にフラッシュを検出した時刻

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            timestamp = frame_idx / fps

            # グレースケールで平均輝度を計算（処理負荷削減のため 1/4 縮小）
            small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            brightness = float(gray.mean())

            # ローディング画面（暗すぎるフレーム）はスキップ
            if brightness < min_brightness:
                prev_brightness = brightness
                continue

            if prev_brightness is not None:
                delta = brightness - prev_brightness

                # 輝度が急上昇 → フラッシュ検出
                if delta >= flash_threshold:
                    if timestamp - last_flash_time >= cooldown_sec:
                        # スコアを flash_threshold を 1.0 とした相対値でクリップ
                        score = min(delta / (flash_threshold * 3), 1.0)
                        events.append(HighlightEvent(
                            timestamp=round(timestamp, 2),
                            event_type="brightness_flash",
                            score=round(score, 3),
                        ))
                        last_flash_time = timestamp

            prev_brightness = brightness

    finally:
        cap.release()

    return sorted(events, key=lambda e: e.timestamp)
