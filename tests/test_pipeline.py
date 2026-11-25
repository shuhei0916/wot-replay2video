"""pipeline の純粋ロジック（リムックス失敗時の後始末）のテスト。"""

from types import SimpleNamespace

import pytest

import src.pipeline as pipeline
import src.upload_youtube as upload_youtube
from src.upload_youtube import record_video_id


class TestDeleteRawIfLongformDisabled:
    def test_deletes_when_longform_disabled_and_shorts_uploaded(self, tmp_path, monkeypatch):
        video_id_log = tmp_path / "video_ids.json"
        monkeypatch.setattr(upload_youtube, "VIDEO_ID_LOG", video_id_log)
        record_video_id("replay_shorts", "abc123", video_id_log)

        recording = tmp_path / "replay.mp4"
        recording.write_bytes(b"data")
        monkeypatch.setattr(
            pipeline, "load_config",
            lambda: {"youtube": {"longform": {"enabled": False}}},
        )

        pipeline._delete_raw_if_longform_disabled(recording, "replay_shorts")

        assert not recording.exists()

    def test_keeps_when_longform_enabled(self, tmp_path, monkeypatch):
        video_id_log = tmp_path / "video_ids.json"
        monkeypatch.setattr(upload_youtube, "VIDEO_ID_LOG", video_id_log)
        record_video_id("replay_shorts", "abc123", video_id_log)

        recording = tmp_path / "replay.mp4"
        recording.write_bytes(b"data")
        monkeypatch.setattr(
            pipeline, "load_config",
            lambda: {"youtube": {"longform": {"enabled": True}}},
        )

        pipeline._delete_raw_if_longform_disabled(recording, "replay_shorts")

        assert recording.exists()

    def test_keeps_when_shorts_upload_not_confirmed(self, tmp_path, monkeypatch):
        video_id_log = tmp_path / "video_ids.json"  # 何も記録しない = Shortsアップロード未確認
        monkeypatch.setattr(upload_youtube, "VIDEO_ID_LOG", video_id_log)

        recording = tmp_path / "replay.mp4"
        recording.write_bytes(b"data")
        monkeypatch.setattr(
            pipeline, "load_config",
            lambda: {"youtube": {"longform": {"enabled": False}}},
        )

        pipeline._delete_raw_if_longform_disabled(recording, "replay_shorts")

        assert recording.exists()


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


# ---- check_camera_events（フリーカメラ対策。2026-11-23） ----

class TestCheckCameraEvents:
    def test_no_camera_events_ok(self):
        pipeline.check_camera_events({"events": [{"type": "shot", "epoch": 1.0}]})

    def test_restored_free_camera_ok(self, capsys):
        pipeline.check_camera_events({"events": [
            {"type": "free_camera", "epoch": 1.0},
            {"type": "camera_restored", "epoch": 2.0},
        ]})
        assert "1 回検出" in capsys.readouterr().out

    def test_restore_failed_raises(self):
        with pytest.raises(pipeline.FreeCameraError):
            pipeline.check_camera_events({"events": [
                {"type": "free_camera", "epoch": 1.0},
                {"type": "camera_restore_failed", "epoch": 2.0},
            ]})

    def test_watcher_error_only_warns(self, capsys):
        # 監視自体のエラーでは録画を捨てない（全録画が捨てられるのを防ぐ）
        pipeline.check_camera_events({"events": [{"type": "camera_check_error", "epoch": 1.0}]})
        assert "警告" in capsys.readouterr().out


# ---- mod 未読込の録画は捨ててバッチを止める（2026-11-25） ----

class TestModNotLoaded:
    def _record(self, tmp_path, with_events):
        rec = tmp_path / "r_20261125_000000.mp4"
        rec.write_bytes(b"x")
        rec.with_suffix(".meta.json").write_text("{}")
        if with_events:
            rec.with_suffix(".events.json").write_text('{"events": [{"type": "camera_state"}]}')
        return rec

    def test_missing_events_discards_and_raises(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, with_events=False)
        monkeypatch.setattr(pipeline, "record_replay", lambda _: rec)
        monkeypatch.setattr(pipeline, "load_config", lambda: {})
        with pytest.raises(pipeline.ModNotLoadedError):
            pipeline.process_replay(tmp_path / "r.wotreplay")
        assert not rec.exists()
        assert not rec.with_suffix(".meta.json").exists()

    def test_is_environment_error(self):
        # batch.py はこれを受けてバッチを中断する
        assert issubclass(pipeline.ModNotLoadedError, pipeline.RecordingEnvironmentError)

    def test_require_mod_false_continues(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, with_events=False)
        monkeypatch.setattr(pipeline, "record_replay", lambda _: rec)
        monkeypatch.setattr(pipeline, "load_config",
                            lambda: {"recording": {"require_mod": False}})
        monkeypatch.setattr(pipeline, "make_highlight_shorts", lambda _: None)
        assert pipeline.process_replay(tmp_path / "r.wotreplay") == rec

    def test_events_present_passes(self, tmp_path, monkeypatch):
        rec = self._record(tmp_path, with_events=True)
        monkeypatch.setattr(pipeline, "record_replay", lambda _: rec)
        monkeypatch.setattr(pipeline, "load_config", lambda: {})
        monkeypatch.setattr(pipeline, "make_highlight_shorts", lambda _: None)
        assert pipeline.process_replay(tmp_path / "r.wotreplay") == rec
