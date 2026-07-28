"""pipeline の純粋ロジック（リムックス失敗時の後始末）のテスト。"""

from types import SimpleNamespace

import pytest

import src.pipeline as pipeline


class TestRemuxFaststart:
    def test_failure_cleans_up_tmp_file(self, tmp_path, monkeypatch):
        src = tmp_path / "recording.mp4"
        src.write_bytes(b"original")

        monkeypatch.setattr(pipeline, "find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)

        def fake_run(cmd, capture_output, **kwargs):
            # ffmpeg 呼び出し自体はディスクフル等で失敗するが、途中生成物の
            # .tmp.mp4 は書き出されている状況を再現する
            tmp = src.with_suffix(".tmp.mp4")
            tmp.write_bytes(b"partial")
            return SimpleNamespace(returncode=1, stderr=b"No space left on device")

        monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="リムックスに失敗"):
            pipeline._remux_faststart(src)

        assert not src.with_suffix(".tmp.mp4").exists()
        assert src.read_bytes() == b"original"  # 元ファイルは上書きされない
