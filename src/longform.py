"""
未編集フル録画をロング動画として YouTube にアップロードするための純粋ロジック。

背景: フル録画 (`<replay_stem>_<ts>.mp4`) は Shorts 抽出後もずっと output/ に
残り続け、2026-07-27 にディスク満杯でバッチが連鎖的に失敗する事故を起こした。
対応する Shorts がアップロード済みになったフル録画をロング動画としてアップロード
し、Shorts の説明欄に「フル試合はこちら」リンクを追記した後、フル録画を削除する
ことで output/ の肥大化を止める。

対象は「Shorts が本モジュール導入後のコードでアップロードされ、video_id が
video_ids.json に記録されているもの」に限られる（過去にアップロード済みの
Shorts は video_id が未記録のため、遡及的にはロング動画化されない）。
"""

import json
import re
from pathlib import Path

from src.config import OUTPUT_DIR
from src.upload_youtube import UPLOAD_LOG, VIDEO_ID_LOG, get_video_id, is_uploaded

LONGFORM_LOG = OUTPUT_DIR / "longform_uploaded.json"

_SHORTS_TAG_RE = re.compile(r"#Shorts\b\s*", re.IGNORECASE)


def _is_raw_recording(path: Path) -> bool:
    return (
        path.suffix == ".mp4"
        and not path.name.endswith("_shorts.mp4")
        and not path.name.endswith(".tmp.mp4")
    )


def collect_pending_longform(
    output_dir: Path = OUTPUT_DIR,
    upload_log: Path = UPLOAD_LOG,
    video_id_log: Path = VIDEO_ID_LOG,
    longform_log: Path = LONGFORM_LOG,
) -> list[Path]:
    """
    ロング動画アップロード対象のフル録画一覧を返す（ファイル名昇順）。

    対象条件:
      - `_shorts.mp4` / `.tmp.mp4` ではない
      - 対応する `.meta.json` が存在する（録画完了の目印。バッチ実行中の
        書き込み途中ファイルを誤って拾わないため）
      - 対応する Shorts (`<stem>_shorts`) が upload_log にアップロード済み
        として記録されている
      - その Shorts の video_id が video_id_log に記録されている
      - まだロング動画アップロード済みでない
    """
    output_dir = Path(output_dir)
    targets = []
    for path in sorted(output_dir.glob("*.mp4")):
        if not _is_raw_recording(path):
            continue
        stem = path.stem
        if is_longform_uploaded(stem, longform_log):
            continue
        if not path.with_suffix(".meta.json").exists():
            continue
        shorts_stem = f"{stem}_shorts"
        if not is_uploaded(shorts_stem, upload_log):
            continue
        if get_video_id(shorts_stem, video_id_log) is None:
            continue
        targets.append(path)
    return targets


def build_longform_title(shorts_title: str, prefix: str = "【ノーカット】") -> str:
    """Shorts タイトルから #Shorts タグを除去し、prefix を前置する。"""
    stripped = _SHORTS_TAG_RE.sub("", shorts_title).rstrip()
    return f"{prefix}{stripped}"


def build_longform_description(shorts_url: str) -> str:
    """ロング動画の説明欄（ショート版へのリンクを含む）。"""
    return f"ショート版はこちら: {shorts_url}"


def build_shorts_backfill_description(longform_url: str) -> str:
    """Shorts 動画の説明欄に追記する「フル試合はこちら」テキスト。"""
    return f"フル試合（ノーカット版）はこちら: {longform_url}"


def is_longform_uploaded(raw_stem: str, log_path: Path = LONGFORM_LOG) -> bool:
    """raw_stem が既にロング動画アップロード済みか確認する。"""
    if not log_path.exists():
        return False
    entries = json.loads(log_path.read_text(encoding="utf-8"))
    return raw_stem in entries


def mark_longform_uploaded(
    raw_stem: str,
    video_id: str,
    shorts_stem: str,
    log_path: Path = LONGFORM_LOG,
) -> None:
    """ロング動画アップロード完了を記録する（backfilled は False で初期化）。"""
    entries: dict = {}
    if log_path.exists():
        entries = json.loads(log_path.read_text(encoding="utf-8"))
    entries[raw_stem] = {
        "video_id": video_id,
        "shorts_stem": shorts_stem,
        "backfilled": False,
    }
    log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def mark_backfilled(raw_stem: str, log_path: Path = LONGFORM_LOG) -> None:
    """Shorts 側への説明欄バックフィルが完了したことを記録する。"""
    entries = json.loads(log_path.read_text(encoding="utf-8"))
    entries[raw_stem]["backfilled"] = True
    log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def pending_backfills(log_path: Path = LONGFORM_LOG) -> list[dict]:
    """バックフィル未完了のエントリ一覧を返す（再試行用）。"""
    if not log_path.exists():
        return []
    entries = json.loads(log_path.read_text(encoding="utf-8"))
    return [
        {"raw_stem": stem, **info}
        for stem, info in entries.items()
        if not info.get("backfilled", False)
    ]
