"""
Shorts アップロード済みのフル録画を「ノーカット版」ロング動画として
YouTube にアップロードし、Shorts の説明欄にリンクを追記した後、
フル録画を削除する。

- 対象は output/ 直下のフル録画のうち、対応する Shorts が既にアップロード
  済み（video_id 記録あり）かつ未ロング動画化のもの（src.longform 参照）
- 1回の実行で youtube.longform.max_per_run 本まで（YouTube API クォータ対策、
  upload_backlog.py と同じクォータ予算を共有するため運用上は Shorts の
  アップロードを先に済ませてから実行する）
- 前回失敗した Shorts 説明欄バックフィルの再試行も毎回行う（videos.update は
  videos.insert よりずっと軽量なクォータ消費のため）

使い方:
    python -u upload_longform_backlog.py
"""

import sys

# コンソールが CP932 でも非対応文字で落ちないようにする
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

from src.config import OUTPUT_DIR, load_config
from src.longform import (
    build_longform_description,
    build_longform_title,
    build_shorts_backfill_description,
    collect_pending_longform,
    mark_backfilled,
    mark_longform_uploaded,
    pending_backfills,
)
from src.upload_youtube import VIDEO_ID_LOG, get_video_id, update_video_description, upload_video


def _retry_pending_backfills() -> None:
    pending = pending_backfills()
    if not pending:
        return
    print(f"バックフィル再試行: {len(pending)} 件")
    for entry in pending:
        raw_stem = entry["raw_stem"]
        shorts_video_id = get_video_id(entry["shorts_stem"], VIDEO_ID_LOG)
        if shorts_video_id is None:
            print(f"  スキップ（Shortsのvideo_id不明）: {raw_stem}")
            continue
        longform_url = f"https://youtu.be/{entry['video_id']}"
        try:
            update_video_description(shorts_video_id, build_shorts_backfill_description(longform_url))
            mark_backfilled(raw_stem)
            print(f"  完了: {raw_stem}")
        except Exception as e:
            print(f"  失敗（次回再試行）: {raw_stem}: {e}")


def main() -> int:
    yt = load_config().get("youtube", {})
    longform_cfg = yt.get("longform", {})
    if not longform_cfg.get("enabled", True):
        print("youtube.longform.enabled: false のため何もしません")
        return 0

    _retry_pending_backfills()

    targets = collect_pending_longform()
    if not targets:
        print("ロング動画アップロード対象のフル録画はありません")
        return 0

    limit = int(longform_cfg.get("max_per_run", 3))
    privacy = longform_cfg.get("privacy", "unlisted")
    title_prefix = longform_cfg.get("title_prefix", "【ノーカット】")
    print(f"アップロード対象: {len(targets)} 本（今回は上位 {limit} 本まで）")

    uploaded = 0
    for raw_path in targets[:limit]:
        stem = raw_path.stem
        shorts_stem = f"{stem}_shorts"
        shorts_video_id = get_video_id(shorts_stem, VIDEO_ID_LOG)
        shorts_url = f"https://youtu.be/{shorts_video_id}"

        title_path = OUTPUT_DIR / f"{shorts_stem}.txt"
        shorts_title = title_path.read_text(encoding="utf-8").strip() if title_path.exists() else stem
        title = build_longform_title(shorts_title, title_prefix)
        description = build_longform_description(shorts_url)

        print(f"\n{raw_path.name}")
        print(f"  タイトル: {title}")

        try:
            video_id = upload_video(
                video_path=raw_path,
                title=title,
                description=description,
                privacy=privacy,
                category_id=yt.get("category_id", "20"),
            )
            if video_id is None:
                # アップロード自体は前回成功したがログ更新前に中断した場合の復旧
                video_id = get_video_id(stem, VIDEO_ID_LOG)
            if video_id is None:
                print("  警告: video_id が確認できずスキップ（削除しません）")
                continue
        except Exception as e:
            msg = str(e)
            if "quota" in msg.lower() or "403" in msg:
                print(f"  クォータ超過とみられるため中断します: {e}")
                break
            print(f"  失敗（スキップ）: {e}")
            continue

        mark_longform_uploaded(stem, video_id, shorts_stem)
        raw_path.unlink()
        uploaded += 1
        print(f"  フル録画を削除しました: {raw_path.name}")

        try:
            update_video_description(
                shorts_video_id, build_shorts_backfill_description(f"https://youtu.be/{video_id}")
            )
            mark_backfilled(stem)
            print("  Shorts説明欄にリンクを追記しました")
        except Exception as e:
            print(f"  警告: Shorts説明欄バックフィルに失敗（次回再試行）: {e}")

    print(f"\n完了: {uploaded} 本をアップロードしました（残り {len(targets) - uploaded} 本）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
