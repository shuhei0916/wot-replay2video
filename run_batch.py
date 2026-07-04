"""
夜間バッチ実行スクリプト。

Google Drive の replays フォルダから、現行クライアントで再生可能な
リプレイを自動選択して処理する:
  1. MIN_DATE 以降のファイルに絞る（旧クライアント世代を除外）
  2. 最新リプレイと同じクライアントバージョンのものだけ残す
  3. processed.json で処理済みを除外（src.batch 側で実施）
"""

import sys
from pathlib import Path

# コンソールが CP932 でも LLM 生成タイトル等の非対応文字（— など）で
# 落ちないようにする（表示は化けてもパイプラインは止めない）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

from src.batch import process_replays
from src.parse_replay import read_replay_version

REPLAY_DIR = Path(r"G:\その他のパソコン\マイ コンピュータ\replays")

# 現行クライアント世代（v2.3.x）の最初のリプレイ日
MIN_DATE = "20260619"


def collect_replays() -> list[Path]:
    candidates = sorted(
        p for p in REPLAY_DIR.glob("*.wotreplay") if p.name[:8] >= MIN_DATE
    )
    if not candidates:
        return []

    # 最新リプレイのバージョン = 現行クライアントで再生可能なバージョン
    target_version = read_replay_version(candidates[-1])
    if not target_version:
        print(f"警告: 最新リプレイのバージョンを取得できません: {candidates[-1].name}")
        return candidates

    matched = [p for p in candidates if read_replay_version(p) == target_version]
    skipped = len(candidates) - len(matched)
    if skipped:
        print(f"バージョン不一致でスキップ: {skipped} 本（対象バージョン: {target_version}）")
    return matched


if __name__ == "__main__":
    replays = collect_replays()
    if not replays:
        print(f"対象リプレイがありません: {REPLAY_DIR}")
        sys.exit(1)
    process_replays(replays)
