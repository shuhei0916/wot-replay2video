"""
アップロード待ちの Shorts をまとめて YouTube にアップロードする。

- output/ 直下の *_shorts.mp4 のうち、タイトル .txt があり未アップロードの
  ものを対象とする
- リプレイの戦績（キル・ダメージ）が良い順にアップロードする
- YouTube API の日次クォータ（videos.insert = 1600 unit、既定 10000 unit/日
  = 実質 6 本）を考慮し、1回の実行で youtube.max_per_run 本まで

使い方:
    python -u upload_backlog.py
"""

import json
import sys
from pathlib import Path

from src.config import OUTPUT_DIR, load_config, replays_dir
from src.parse_replay import parse_replay
from src.pipeline import MIN_AUDIO_BITRATE, _audio_bitrate
from src.upload_youtube import (
    UPLOAD_LOG,
    is_uploaded,
    replay_stem_from_video,
    upload_video,
)


def collect_pending(
    output_dir: Path | None = None,
    replay_dir: Path | None = None,
    upload_log: Path | None = None,
) -> list[tuple[int, Path, str]]:
    """(優先度スコア, 動画パス, タイトル) のリストをスコア降順で返す。"""
    output_dir = output_dir if output_dir is not None else OUTPUT_DIR
    replay_dir = replay_dir if replay_dir is not None else replays_dir()
    upload_log = upload_log if upload_log is not None else UPLOAD_LOG

    items = []
    for p in sorted(output_dir.glob("*_shorts.mp4")):
        if is_uploaded(p.stem, upload_log):
            continue
        title_path = p.with_suffix(".txt")
        if not title_path.exists():
            continue  # タイトル未生成（旧形式ファイル等）は対象外
        bitrate = _audio_bitrate(p)
        if bitrate is not None and bitrate < MIN_AUDIO_BITRATE:
            print(f"  除外（無音）: {p.name}")
            continue
        title = title_path.read_text(encoding="utf-8").strip()

        score = 0
        replay = replay_dir / (replay_stem_from_video(p.stem) + ".wotreplay")
        try:
            s = parse_replay(replay).player_stats
            score = s.kills * 1000 + s.damage_dealt
        except Exception:
            pass
        items.append((score, p, title))

    return sorted(items, key=lambda x: -x[0])


def main() -> int:
    yt = load_config().get("youtube", {})
    if not yt.get("enabled", True):
        print("youtube.enabled: false のため何もしません")
        return 0

    pending = collect_pending()
    if not pending:
        print("アップロード待ちの Shorts はありません")
        return 0

    limit = int(yt.get("max_per_run", 5))
    print(f"アップロード待ち: {len(pending)} 本（今回は上位 {limit} 本まで）")

    uploaded = 0
    for score, path, title in pending[:limit]:
        print(f"\n[score={score}] {path.name}")
        print(f"  タイトル: {title}")

        # 多言語タイトルのサイドカーがあれば localizations として使う
        localizations = None
        titles_path = path.with_suffix(".titles.json")
        if titles_path.exists():
            try:
                localizations = json.loads(titles_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass

        try:
            video_id = upload_video(
                video_path=path,
                title=title,
                privacy=yt.get("privacy", "private"),
                category_id=yt.get("category_id", "20"),
                extra_tags=yt.get("default_tags", []),
                localizations=localizations,
            )
            if video_id:
                uploaded += 1
        except Exception as e:
            msg = str(e)
            if "quota" in msg.lower() or "403" in msg:
                print(f"  クォータ超過とみられるため中断します: {e}")
                break
            print(f"  失敗（スキップ）: {e}")

    print(f"\n完了: {uploaded} 本をアップロードしました（残り {len(pending) - uploaded} 本）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
