"""detect_mod_events の純粋ロジックと サイドカー読み込みのテスト。"""

import json
import random

from src.detect_highlights import HighlightEvent
from src.detect_mod_events import (
    BASE_SCORE,
    DUEL_BONUS,
    DUEL_WINDOW_SEC,
    FAST_RELOAD_SEC,
    HIT_TAKEN_BASE_SCORE,
    HIT_TAKEN_MIN_PCT,
    JITTER_RATIO,
    POST_SEC_RANGE,
    PRE_SEC_RANGE,
    SLOW_RELOAD_SEC,
    convert_events,
    load_mod_events,
    pacing_for_interval,
    score_with_audio,
)
from src.edit_video import CLIP_POST_SEC, CLIP_PRE_SEC

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


# ---- pacing_for_interval: 可変長クリップの基準値+ジッター ----

def _within_jitter(value: float, basis: float) -> bool:
    lo, hi = basis * (1 - JITTER_RATIO), basis * (1 + JITTER_RATIO)
    return lo - 1e-6 <= value <= hi + 1e-6


class TestPacingForInterval:
    def test_none_interval_uses_fixed_defaults_as_basis(self):
        rng = random.Random(1)
        pre, post = pacing_for_interval(None, rng)
        assert _within_jitter(pre, CLIP_PRE_SEC)
        assert _within_jitter(post, CLIP_POST_SEC)

    def test_none_interval_still_jittered_not_always_identical(self):
        # 直前射撃が無いケースでも毎回同じ長さにはならない
        results = {pacing_for_interval(None, random.Random(seed)) for seed in range(5)}
        assert len(results) > 1

    def test_fast_reload_gives_shortest_clip(self):
        rng = random.Random(2)
        pre, post = pacing_for_interval(FAST_RELOAD_SEC, rng)
        assert _within_jitter(pre, PRE_SEC_RANGE[0])
        assert _within_jitter(post, POST_SEC_RANGE[0])

    def test_faster_than_fast_reload_clamped(self):
        rng = random.Random(3)
        pre, post = pacing_for_interval(0.5, rng)
        assert _within_jitter(pre, PRE_SEC_RANGE[0])
        assert _within_jitter(post, POST_SEC_RANGE[0])

    def test_slow_reload_gives_longest_clip(self):
        rng = random.Random(4)
        pre, post = pacing_for_interval(SLOW_RELOAD_SEC, rng)
        assert _within_jitter(pre, PRE_SEC_RANGE[1])
        assert _within_jitter(post, POST_SEC_RANGE[1])

    def test_slower_than_slow_reload_clamped(self):
        rng = random.Random(5)
        pre, post = pacing_for_interval(60.0, rng)
        assert _within_jitter(pre, PRE_SEC_RANGE[1])
        assert _within_jitter(post, POST_SEC_RANGE[1])

    def test_mid_interval_interpolates_between_range(self):
        mid = (FAST_RELOAD_SEC + SLOW_RELOAD_SEC) / 2
        rng = random.Random(6)
        pre, post = pacing_for_interval(mid, rng)
        expected_pre = (PRE_SEC_RANGE[0] + PRE_SEC_RANGE[1]) / 2
        expected_post = (POST_SEC_RANGE[0] + POST_SEC_RANGE[1]) / 2
        assert _within_jitter(pre, expected_pre)
        assert _within_jitter(post, expected_post)


# ---- convert_events: dynamic_pacing ----

class TestConvertEventsDynamicPacing:
    def test_disabled_keeps_pre_post_none(self):
        events = convert_events(_data([10.0, 14.0]), REC_START, dynamic_pacing=False)
        assert all(e.pre_sec is None and e.post_sec is None for e in events)

    def test_enabled_sets_pre_post_on_shots(self):
        events = convert_events(_data([10.0, 14.0]), REC_START, dynamic_pacing=True)
        assert all(e.pre_sec is not None and e.post_sec is not None for e in events)

    def test_first_shot_still_gets_pacing(self):
        # 直前射撃が無い最初の1発も固定長そのままではなくジッター済みの値になる
        events = convert_events(_data([10.0]), REC_START, dynamic_pacing=True)
        assert events[0].pre_sec is not None

    def test_fast_interval_shot_gets_short_clip(self):
        # 2発が FAST_RELOAD_SEC 以下の間隔で連射 → 2発目は最短クリップ域
        events = convert_events(_data([10.0, 10.0 + FAST_RELOAD_SEC - 1.0]), REC_START, dynamic_pacing=True)
        second = events[1]
        assert second.pre_sec <= PRE_SEC_RANGE[0] * (1 + JITTER_RATIO) + 1e-6

    def test_slow_interval_shot_gets_long_clip(self):
        # 2発目が SLOW_RELOAD_SEC 以上の間隔をあけて発射 → 最長クリップ域
        events = convert_events(_data([10.0, 10.0 + SLOW_RELOAD_SEC + 5.0]), REC_START, dynamic_pacing=True)
        second = events[1]
        assert second.post_sec >= POST_SEC_RANGE[1] * (1 - JITTER_RATIO) - 1e-6

    def test_duel_bonus_preserves_pacing(self):
        # 撃ち合いボーナスでスコアが変わっても pre_sec/post_sec は維持される
        data = {"events": [
            {"epoch": REC_START + 10.0, "type": "shot"},
            {"epoch": REC_START + 10.0 + DUEL_WINDOW_SEC - 0.1, "type": "hit_taken", "damage_pct": 0.05},
        ]}
        events = convert_events(data, REC_START, dynamic_pacing=True)
        shot = [e for e in events if e.event_type == "shot_mod"][0]
        assert shot.pre_sec is not None and shot.post_sec is not None


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

    def test_preserves_pacing_fields(self):
        mod = convert_events(_data([10.0]), REC_START, dynamic_pacing=True)
        scored = score_with_audio(mod, [_audio(10.3, score=1.0)])
        assert scored[0].pre_sec == mod[0].pre_sec
        assert scored[0].post_sec == mod[0].post_sec


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


# ---- mod v7: 与ダメ・命中の集計（2026-11-27） ----

def test_combat_summary():
    from src.detect_mod_events import combat_summary
    data = {"events": [
        {"type": "shot"}, {"type": "shot"}, {"type": "shot"},
        {"type": "hit_dealt", "damage": 270, "damage_factor": 71},
        {"type": "damage_dealt", "damage": 270, "killed": False},
        {"type": "hit_dealt", "damage": 0, "damage_factor": 0},   # 非貫通
        {"type": "damage_dealt", "damage": 151, "killed": True, "reason": 2},  # 体当たり
        {"type": "hit_taken", "damage": 300},
    ]}
    assert combat_summary(data) == {"shots": 3, "hits": 2, "no_damage_hits": 1, "damage": 421, "kills": 1,
                                    "blocked_hits": 0, "blind_kills": 0}


def test_combat_summary_matches_real_battle():
    # 実機記録（Chi-Go / Ensk、戦闘結果: 射撃13・命中11・与ダメ2548・撃破2）
    import json
    from pathlib import Path
    from src.detect_mod_events import combat_summary
    p = Path(__file__).parent / "fixtures" / "mod_v7_chigo_ensk.events.json"
    m = combat_summary(json.loads(p.read_text(encoding="utf-8")))
    assert (m["shots"], m["hits"], m["damage"], m["kills"]) == (13, 11, 2548, 2)
    assert m["no_damage_hits"] == 2



# ---- mod v7: 射撃の結果でスコアを決める（2026-11-27） ----

def _v7(events):
    return {"events": [dict(e, epoch=REC_START + e["t"]) for e in events]}


class TestOutcomeScoring:
    def test_kill_damage_nopen_miss(self):
        from src.detect_mod_events import (SCORE_DAMAGE_RANGE, SCORE_KILL, SCORE_MISS,
                                           SCORE_NO_DAMAGE_HIT)
        data = _v7([
            {"t": 10, "type": "shot"},
            {"t": 10.2, "type": "hit_dealt", "damage_factor": 60},
            {"t": 10.2, "type": "damage_dealt", "damage": 300, "killed": False, "reason": 0},
            {"t": 30, "type": "shot"},
            {"t": 30.1, "type": "hit_dealt", "damage_factor": 0},
            {"t": 50, "type": "shot"},
            {"t": 70, "type": "shot"},
            {"t": 70.1, "type": "hit_dealt", "damage_factor": 40},
            {"t": 70.1, "type": "damage_dealt", "damage": 150, "killed": True, "reason": 0},
        ])
        ev = {e.timestamp: e for e in convert_events(data, REC_START, dynamic_pacing=False)}
        assert ev[10.0].event_type == "shot_mod_damage" and ev[10.0].score == SCORE_DAMAGE_RANGE[1]
        assert ev[30.0].event_type == "shot_mod_nopen" and ev[30.0].score == SCORE_NO_DAMAGE_HIT
        assert ev[50.0].event_type == "shot_mod_miss" and ev[50.0].score == SCORE_MISS
        assert ev[70.0].event_type == "shot_mod_kill" and ev[70.0].score == SCORE_KILL
        assert ev[70.0].score > ev[10.0].score > ev[30.0].score > ev[50.0].score

    def test_damage_scaled_by_battle_max(self):
        data = _v7([
            {"t": 10, "type": "shot"}, {"t": 10.1, "type": "damage_dealt", "damage": 400, "reason": 0},
            {"t": 30, "type": "shot"}, {"t": 30.1, "type": "damage_dealt", "damage": 100, "reason": 0},
        ])
        ev = {e.timestamp: e for e in convert_events(data, REC_START, dynamic_pacing=False)}
        assert ev[10.0].score > ev[30.0].score

    def test_result_attributed_to_latest_shot_in_window(self):
        data = _v7([
            {"t": 10, "type": "shot"}, {"t": 12, "type": "shot"},
            {"t": 12.3, "type": "damage_dealt", "damage": 200, "reason": 0},
        ])
        ev = {e.timestamp: e for e in convert_events(data, REC_START, dynamic_pacing=False)}
        assert ev[10.0].event_type == "shot_mod_miss"
        assert ev[12.0].event_type == "shot_mod_damage"

    def test_ram_kill_becomes_own_event(self):
        data = _v7([
            {"t": 10, "type": "shot"}, {"t": 10.1, "type": "hit_dealt", "damage_factor": 0},
            {"t": 60, "type": "damage_dealt", "damage": 151, "killed": True, "reason": 2},
        ])
        ev = convert_events(data, REC_START, dynamic_pacing=False)
        other = [e for e in ev if e.event_type == "damage_mod_other"]
        assert len(other) == 1 and other[0].timestamp == 60.0

    def test_legacy_records_keep_base_score(self):
        ev = convert_events(_data([10.0, 30.0]), REC_START, dynamic_pacing=False)
        assert all(e.event_type == "shot_mod" and e.score == BASE_SCORE for e in ev)

    def test_audio_bonus_small_for_outcome_shots(self):
        from src.detect_mod_events import OUTCOME_AUDIO_BONUS_MAX, SCORE_MISS
        data = _v7([{"t": 10, "type": "shot"}, {"t": 30, "type": "shot"},
                    {"t": 30.1, "type": "hit_dealt", "damage_factor": 0}])
        mod = convert_events(data, REC_START, dynamic_pacing=False)
        scored = {e.timestamp: e for e in score_with_audio(mod, [_audio(10.0, score=1.0)])}
        assert scored[10.0].score == round(SCORE_MISS + OUTCOME_AUDIO_BONUS_MAX, 3)

    def test_real_battle_ordering(self):
        import json
        from pathlib import Path
        p = Path(__file__).parent / "fixtures" / "mod_v7_chigo_ensk.events.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        start = data["events"][0]["epoch"]
        ev = convert_events(data, start, dynamic_pacing=False)
        kinds = [e.event_type for e in ev]
        assert kinds.count("shot_mod_kill") == 1        # 射撃での撃破1（もう1両は体当たり）
        assert kinds.count("damage_mod_other") == 1
        assert kinds.count("shot_mod_nopen") == 2
        assert kinds.count("shot_mod_miss") == 2
        assert kinds.count("shot_mod_damage") == 8


def test_combat_summary_v8_real_battle():
    # mod v8 の実機記録（T29 / Karelia）。戦闘結果: 射撃18・命中16（貫通15+非貫通1）・
    # 与ダメ4605・撃破2・被命中8（うち弾いた5）・撃破された車両19。
    # mod は貫通を1発（338）取りこぼす: 視認外の敵への射撃（ブラインドショット）とみられる
    import json
    from pathlib import Path
    from src.detect_mod_events import combat_summary
    p = Path(__file__).parent / "fixtures" / "mod_v8_t29_karelia.events.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    m = combat_summary(data)
    assert (m["shots"], m["hits"], m["no_damage_hits"], m["kills"]) == (18, 15, 1, 2)
    assert m["damage"] == 4605 - 338
    assert m["blocked_hits"] == 5
    events = data["events"]
    assert sum(1 for e in events if e["type"] == "hit_received") == 8
    assert sum(1 for e in events if e["type"] == "vehicle_killed") == 19
    info = next(e for e in events if e["type"] == "arena_info")
    assert len(info["vehicles"]) == 30 and info["player_team"] in (1, 2)
    assert len(data["others"]) > 0


# ---- ブラインド撃破（視認外の敵の撃破。2026-11-28） ----

class TestBlindKills:
    ME = 100

    def _data(self, events):
        info = {"type": "arena_info", "player_vehicle": self.ME, "player_team": 1, "vehicles": {}}
        return {"events": [dict(info, epoch=REC_START)] + [dict(e, epoch=REC_START + e["t"]) for e in events]}

    def test_kill_feed_only_kill_by_me_is_blind(self):
        from src.detect_mod_events import blind_kills
        data = self._data([
            {"t": 10, "type": "shot"},
            {"t": 11, "type": "vehicle_killed", "target": 201, "attacker": self.ME, "reason": 0},
        ])
        assert [e["target"] for e in blind_kills(data)] == [201]

    def test_kill_elsewhere_at_same_time_is_not_picked(self):
        # ユーザー懸念: 射撃と同時刻にマップの別の場所で他の車両が撃破したもの → 撃破者が違うので拾わない
        from src.detect_mod_events import blind_kills
        data = self._data([
            {"t": 10, "type": "shot"},
            {"t": 10.5, "type": "vehicle_killed", "target": 202, "attacker": 555, "reason": 0},
        ])
        assert blind_kills(data) == []

    def test_visible_kill_is_not_blind(self):
        from src.detect_mod_events import blind_kills
        data = self._data([
            {"t": 10, "type": "damage_dealt", "target": 203, "damage": 150, "killed": True, "reason": 0},
            {"t": 10, "type": "vehicle_killed", "target": 203, "attacker": self.ME, "reason": 0},
        ])
        assert blind_kills(data) == []

    def test_final_blow_unseen_after_visible_damage_is_blind(self):
        # 見えている間に削り、見えなくなってから撃破 → 撃破は damage_dealt に残らないのでブラインド扱い
        from src.detect_mod_events import blind_kills
        data = self._data([
            {"t": 10, "type": "damage_dealt", "target": 204, "damage": 300, "killed": False, "reason": 0},
            {"t": 30, "type": "vehicle_killed", "target": 204, "attacker": self.ME, "reason": 0},
        ])
        assert [e["target"] for e in blind_kills(data)] == [204]

    def test_my_own_death_is_ignored(self):
        from src.detect_mod_events import blind_kills
        data = self._data([{"t": 50, "type": "vehicle_killed", "target": self.ME, "attacker": self.ME, "reason": 3}])
        assert blind_kills(data) == []

    def test_v7_without_arena_info_returns_empty(self):
        from src.detect_mod_events import blind_kills
        assert blind_kills({"events": [{"type": "vehicle_killed", "target": 1, "attacker": 2, "epoch": 1}]}) == []

    def test_real_v8_battle_has_no_blind_kill(self):
        # T29 / Karelia: 撃破2はどちらも見えていた（取りこぼした1発は撃破ではない）
        import json
        from pathlib import Path
        from src.detect_mod_events import blind_kills
        p = Path(__file__).parent / "fixtures" / "mod_v8_t29_karelia.events.json"
        assert blind_kills(json.loads(p.read_text(encoding="utf-8"))) == []
