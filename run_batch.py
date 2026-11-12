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
from src.config import replays_dir
from src.parse_replay import read_replay_version

# 現行クライアント世代（v2.3.x）の最初のリプレイ日
MIN_DATE = "20260619"


def collect_replays(replay_dir: Path | None = None) -> list[Path]:
    replay_dir = replay_dir if replay_dir is not None else replays_dir()
    # 先頭8桁が日付のファイルのみ対象。ゲームが書き込み中の temp.wotreplay 等を
    # 除外する（辞書順比較だと "temp..." が MIN_DATE を超えてしまい、さらに
    # sorted() の最後尾に来てバージョン判定まで壊す）
    candidates = sorted(
        p for p in replay_dir.glob("*.wotreplay")
        if p.name[:8].isdigit() and p.name[:8] >= MIN_DATE
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


class _Tee:
    """標準出力をコンソールとログファイルの両方に書く（リモートから進捗を確認するため）。"""

    def __init__(self, stream, log_file):
        self._stream = stream
        self._log = log_file

    def write(self, s):
        self._stream.write(s)
        self._log.write(s)
        self._log.flush()

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _start_logging() -> Path:
    import datetime
    from src.config import OUTPUT_DIR
    log_dir = OUTPUT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"batch_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
    f = open(path, "w", encoding="utf-8", errors="replace")
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)
    return path


if __name__ == "__main__":
    print(f"ログ: {_start_logging()}")
    replays = collect_replays()
    if not replays:
        print(f"対象リプレイがありません: {replays_dir()}")
        sys.exit(1)
    process_replays(replays)
