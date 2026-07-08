"""upload_backlog.collect_pending の選定ロジックのテスト。

ffprobe が偽動画を解析できない場合 _audio_bitrate は None を返し
「無音とは判定されない」ため、無音除外以外のロジックを検証できる。
"""

import json
import shutil
from pathlib import Path

from upload_backlog import collect_pending

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / \
    "20260604_1729_china-Ch20_Type58_115_sweden.wotreplay"


def _make_shorts(dir_: Path, stem: str, title: str | None = "タイトル") -> Path:
    p = dir_ / f"{stem}_shorts.mp4"
    p.write_bytes(b"fake video")
    if title is not None:
        p.with_suffix(".txt").write_text(title, encoding="utf-8")
    return p


class TestCollectPending:
    def test_empty_dir(self, tmp_path):
        assert collect_pending(tmp_path, tmp_path, tmp_path / "up.json") == []

    def test_requires_title_txt(self, tmp_path):
        _make_shorts(tmp_path, "video_20260101_000000", title=None)
        assert collect_pending(tmp_path, tmp_path, tmp_path / "up.json") == []

    def test_uploaded_excluded(self, tmp_path):
        p = _make_shorts(tmp_path, "video_20260101_000000")
        log = tmp_path / "up.json"
        log.write_text(json.dumps([p.stem]), encoding="utf-8")
        assert collect_pending(tmp_path, tmp_path, log) == []

    def test_pending_included_with_title(self, tmp_path):
        _make_shorts(tmp_path, "video_20260101_000000", title="弾あたらん😡")
        items = collect_pending(tmp_path, tmp_path, tmp_path / "up.json")
        assert len(items) == 1
        score, path, title = items[0]
        assert title == "弾あたらん😡"
        assert score == 0  # 対応リプレイなし → スコア0

    def test_scored_by_replay_stats_and_sorted(self, tmp_path):
        # フィクスチャリプレイ (1kill/974dmg → score 1974) に対応する Shorts
        replay_dir = tmp_path / "replays"
        replay_dir.mkdir()
        shutil.copy(FIXTURE_REPLAY, replay_dir / FIXTURE_REPLAY.name)
        stem_with_replay = FIXTURE_REPLAY.stem + "_20260101_000000"

        out = tmp_path / "out"
        out.mkdir()
        _make_shorts(out, "unknown_20260101_000000")       # score 0
        _make_shorts(out, stem_with_replay)                # score 1974

        items = collect_pending(out, replay_dir, tmp_path / "up.json")
        assert [i[0] for i in items] == [1974, 0]  # スコア降順
