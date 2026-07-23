"""detect_mod_events の純粋ロジックと サイドカー読み込みのテスト。"""

import json

from src.detect_highlights import HighlightEvent
from src.detect_mod_events import (
    BASE_SCORE,
    DUEL_BONUS,
    DUEL_WINDOW_SEC,
    HIT_TAKEN_BASE_SCORE,
    HIT_TAKEN_MIN_PCT,
    convert_events,
    load_mod_events,
    score_with_audio,
)

REC_START = 1783148000.0


def _data(offsets):
    return {"events": [{"epoch": REC_START + t, "type": "shot"} for t in offsets]}


# ---- convert_events ----

class TestConvertEvents:
    def test_empty(self):
        assert convert_events({}, REC_START) == []

    def test_epoch_to_video_timestamp(self):
        events = convert_events(_data([117.5, 274.2]), REC_START)
        assert [e.timestamp for e in events] == [117.5, 274.2]
        assert all(e.event_type == "shot_mod" for e in events)
        assert all(e.score == BASE_SCORE for e in events)

    def test_negative_ts_excluded(self):
        # 録画開始前のイベント（ローディング中など）は除外
        events = convert_events(_data([-5.0, 10.0]), REC_START)
        assert [e.timestamp for e in events] == [10.0]

    def test_max_ts_filter(self):
        events = convert_events(_data([10.0, 500.0]), REC_START, max_ts=400.0)
        assert [e.timestamp for e in events] == [10.0]

    def test_death_events_not_converted_to_clips(self):
        # death は録画打ち切り用のイベントで、ハイライトクリップにはしない
        data = {"events": [
            {"epoch": REC_START + 10.0, "type": "shot"},
            {"epoch": REC_START + 200.0, "type": "death"},
        ]}
        events = convert_events(data, REC_START)
        assert [e.event_type for e in events] == ["shot_mod"]


# ---- hit_taken: (a) 撃ち合いボーナス / (b) 大きな一撃の単独ハイライト化 ----

class TestHitTaken:
    def test_small_hit_not_standalone_clip(self):
        # 閾値未満の被弾は単独ハイライトにしない
        data = {"events": [
            {"epoch": REC_START + 50.0, "type": "hit_taken", "damage_pct": HIT_TAKEN_MIN_PCT - 0.01},
        ]}
        events = convert_events(data, REC_START)
        assert events == []

    def test_big_hit_becomes_standalone_clip(self):
        data = {"events": [
            {"epoch": REC_START + 50.0, "type": "hit_taken", "damage_pct": HIT_TAKEN_MIN_PCT + 0.1},
        ]}
        events = convert_events(data, REC_START)
        assert len(events) == 1
        assert events[0].event_type == "hit_taken"
        assert events[0].timestamp == 50.0
        assert events[0].score == HIT_TAKEN_BASE_SCORE

    def test_hit_taken_without_pct_not_standalone_clip(self):
        # damage_pct が計算できなかった被弾（maxHealth 推定失敗等）は単独化しない
        data = {"events": [
            {"epoch": REC_START + 50.0, "type": "hit_taken", "damage": 300},
        ]}
        events = convert_events(data, REC_START)
        assert events == []

    def test_shot_followed_by_hit_gets_duel_bonus(self):
        data = {"events": [
            {"epoch": REC_START + 10.0, "type": "shot"},
            {"epoch": REC_START + 10.0 + DUEL_WINDOW_SEC - 0.1, "type": "hit_taken", "damage_pct": 0.05},
        ]}
        events = convert_events(data, REC_START)
        shot = [e for e in events if e.event_type == "shot_mod"][0]
        assert shot.score == round(min(BASE_SCORE + DUEL_BONUS, 1.0), 3)

    def test_shot_without_nearby_hit_keeps_base_score(self):
        data = {"events": [
            {"epoch": REC_START + 10.0, "type": "shot"},
            {"epoch": REC_START + 10.0 + DUEL_WINDOW_SEC + 1.0, "type": "hit_taken", "damage_pct": 0.05},
        ]}
        events = convert_events(data, REC_START)
        shot = [e for e in events if e.event_type == "shot_mod"][0]
        assert shot.score == BASE_SCORE

    def test_hit_before_shot_is_not_a_duel_bonus(self):
        # 被弾は射撃の「直後」のみ撃ち合いとみなす（先に食らった被弾は無関係）
        data = {"events": [
            {"epoch": REC_START + 10.0, "type": "hit_taken", "damage_pct": 0.05},
            {"epoch": REC_START + 10.0 + DUEL_WINDOW_SEC + 5.0, "type": "shot"},
        ]}
        events = convert_events(data, REC_START)
        shot = [e for e in events if e.event_type == "shot_mod"][0]
        assert shot.score == BASE_SCORE


# ---- death_epoch_from_events (pipeline) ----

class TestDeathEpoch:
    def _find(self, data):
        from src.pipeline import death_epoch_from_events
        return death_epoch_from_events(data)

    def test_no_events(self):
        assert self._find({}) is None
        assert self._find({"events": []}) is None

    def test_no_death(self):
        assert self._find(_data([10.0, 20.0])) is None

    def test_death_epoch_returned(self):
        data = {"events": [
            {"epoch": REC_START + 10.0, "type": "shot"},
            {"epoch": REC_START + 200.0, "type": "death"},
        ]}
        assert self._find(data) == REC_START + 200.0

    def test_first_death_wins(self):
        data = {"events": [
            {"epoch": 100.0, "type": "death"},
            {"epoch": 200.0, "type": "death"},
        ]}
        assert self._find(data) == 100.0

    def test_broken_epoch_returns_none(self):
        assert self._find({"events": [{"type": "death", "epoch": "broken"}]}) is None

    def test_non_shot_types_ignored(self):
        data = {"events": [
            {"epoch": REC_START + 5, "type": "battle_start"},
            {"epoch": REC_START + 10, "type": "shot"},
        ]}
        events = convert_events(data, REC_START)
        assert len(events) == 1

    def test_sorted_by_timestamp(self):
        events = convert_events(_data([50.0, 10.0, 30.0]), REC_START)
        ts = [e.timestamp for e in events]
        assert ts == sorted(ts)


# ---- score_with_audio ----

def _audio(t, score=1.0):
    return HighlightEvent(timestamp=t, event_type="shot_audio", score=score)


class TestScoreWithAudio:
    def test_no_audio_keeps_base_score(self):
        mod = convert_events(_data([10.0]), REC_START)
        scored = score_with_audio(mod, [])
        assert scored[0].score == BASE_SCORE

    def test_nearby_audio_boosts(self):
        mod = convert_events(_data([10.0]), REC_START)
        scored = score_with_audio(mod, [_audio(10.3, score=1.0)])
        assert scored[0].score == 1.0  # 0.7 + 0.3*1.0

    def test_partial_audio_score(self):
        mod = convert_events(_data([10.0]), REC_START)
        scored = score_with_audio(mod, [_audio(10.0, score=0.5)])
        assert scored[0].score == 0.85  # 0.7 + 0.3*0.5

    def test_distant_audio_no_boost(self):
        mod = convert_events(_data([10.0]), REC_START)
        scored = score_with_audio(mod, [_audio(15.0, score=1.0)])
        assert scored[0].score == BASE_SCORE


# ---- load_mod_events (サイドカー読み込み) ----

class TestLoadModEvents:
    def test_missing_sidecars_returns_none(self, tmp_path):
        rec = tmp_path / "recording.mp4"
        rec.write_bytes(b"")
        assert load_mod_events(rec) is None

    def test_reads_sidecars(self, tmp_path):
        rec = tmp_path / "recording.mp4"
        rec.write_bytes(b"")
        rec.with_suffix(".meta.json").write_text(
            json.dumps({"rec_start_epoch": REC_START}), encoding="utf-8")
        rec.with_suffix(".events.json").write_text(
            json.dumps(_data([117.5, 138.2])), encoding="utf-8")
        events = load_mod_events(rec)
        assert events is not None
        assert [e.timestamp for e in events] == [117.5, 138.2]

    def test_broken_meta_returns_none(self, tmp_path):
        rec = tmp_path / "recording.mp4"
        rec.write_bytes(b"")
        rec.with_suffix(".meta.json").write_text("not json", encoding="utf-8")
        rec.with_suffix(".events.json").write_text(json.dumps(_data([1.0])), encoding="utf-8")
        assert load_mod_events(rec) is None

    def test_empty_events_returns_none(self, tmp_path):
        rec = tmp_path / "recording.mp4"
        rec.write_bytes(b"")
        rec.with_suffix(".meta.json").write_text(
            json.dumps({"rec_start_epoch": REC_START}), encoding="utf-8")
        rec.with_suffix(".events.json").write_text(json.dumps({"events": []}), encoding="utf-8")
        assert load_mod_events(rec) is None
