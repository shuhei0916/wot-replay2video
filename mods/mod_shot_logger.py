# -*- coding: utf-8 -*-
# WoT の組み込み Python は 2.7。py2.7 でコンパイルした .pyc を
# res_mods/<version>/scripts/client/ に配置する（ソース .py は無視される）。
"""
mod_shot_logger v6 - リプレイ再生中の射撃・被弾イベントを shot_events.json に記録する。

設計:
- 各イベントに壁時計時刻 (epoch) を記録する。録画開始時刻(パイプライン側が
  持っている)との差分で動画内タイムスタンプに変換できるため、
  ゲーム内時計やバトル開始検出に依存しない。
- Vehicle.showShooting フック: 自車の射撃を検知（import 時に安全に張れる）
- Vehicle.onHealthChanged フック: 自車の被弾 (hit_taken) と撃破 (death) を検知。
  被弾はダメージ量を記録し、パイプライン側が (a) 直後の射撃イベントの
  「撃ち合い」加点、(b) 残HPを大きく削る一撃の単独ハイライト候補化に使う。
  死亡はパイプライン側の録画早期打ち切りに使う。
  実測で self.health はフック呼び出し時点で既に newHealth 相当に更新済み
  （self.health との差分では常に damage=0 になり被弾を検知できなかった）
  と判明したため、直前HPはモジュール側の _last_known_health で自前追跡する。
  満タンHP（damage_pct 計算用）は self.maxHealth /
  self.typeDescriptor.maxHealth を試し、両方失敗しても hit_taken の記録
  自体（撃ち合い判定用）は継続する。
- PlayerAvatar.onArenaPeriodChange フック: バトル開始 (period=3) の記録。
  personality 読み込み時点では Avatar モジュールが未初期化のため、
  BigWorld.callback で遅延リトライして張る。
  ※実測では一度も発火していない（todo.md 参照）。death 検知には使わない。
- AutoShootGunController フック（v6）: Vz. 71 Tesák 等の連射砲（オートシュート砲）は
  showShooting を通らないため、射撃状態の変化 (set_stateStatus) と単発
  (onDiscreteShot) を別に拾う。連射中は AUTO_SHOT_INTERVAL_SEC ごとに shot を記録する。
- フリーカメラ自動復帰（v6）: リプレイ再生中に左クリックされると追従カメラ
  (bound) からフリーカメラ (unbound) に切り替わり、録画が台無しになる
  （2026-11-23 に 3 本削除）。バッチ録画中（BATCH_FLAG_PATH がある間）だけ
  1 秒ごとに監視し、フリーカメラなら BattleReplay.__gotoBoundMode() で戻す。
  free_camera / camera_restored / camera_restore_failed を記録し、
  パイプラインは restore_failed があればその録画を捨てる。
  手動でリプレイを見るとき（フラグなし）はフリーカメラを妨げない。
"""

import json
import os
import time

MOD_NAME = 'mod_shot_logger'
OUTPUT_PATH = '__WOT_DIR__/shot_events.json'
# パイプラインが録画中だけ作るフラグ。あるときだけフリーカメラを自動で戻す
BATCH_FLAG_PATH = '__WOT_DIR__/replay2video_recording.flag'

_events = []
_positions = []  # [[epoch, x, z], ...] 自車のワールド座標（1秒ごと。長距離移動の検出用）
_arena_start_epoch = None
_death_recorded = False
_last_known_health = None  # 自前追跡する直前HP（onHealthChanged 間で引き継ぐ）

POSITION_INTERVAL_SEC = 1.0
AUTO_SHOT_INTERVAL_SEC = 2.0  # 連射中に shot を刻む間隔（ハイライト候補の密度）


def _write():
    try:
        f = open(OUTPUT_PATH, 'w')
        json.dump({'arena_start_epoch': _arena_start_epoch,
                   'events': _events,
                   'positions': _positions}, f)
        f.close()
    except Exception:
        pass


def _record(kind, **extra):
    ev = {'epoch': round(time.time(), 3), 'type': kind}
    ev.update(extra)
    _events.append(ev)
    _write()


import BigWorld  # noqa: E402

_write()  # ロード確認用の初期書き出し
BigWorld.logInfo(MOD_NAME, 'v5 loaded. output -> ' + OUTPUT_PATH, None)


# --------------------------------------------------------------------------
# Vehicle.showShooting: 自車の射撃検知
# --------------------------------------------------------------------------

try:
    import Vehicle as _Vehicle

    _orig_showShooting = _Vehicle.Vehicle.showShooting

    def _hooked_showShooting(self, *args, **kwargs):
        try:
            player = BigWorld.player()
            pid = getattr(player, 'playerVehicleID', None)
            if pid is not None and getattr(self, 'id', None) == pid:
                _record('shot')
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'shot hook error: ' + str(ex), None)
        return _orig_showShooting(self, *args, **kwargs)

    _Vehicle.Vehicle.showShooting = _hooked_showShooting
    BigWorld.logInfo(MOD_NAME, 'Vehicle.showShooting hook installed', None)
except Exception as ex:
    BigWorld.logWarning(MOD_NAME, 'Vehicle hook failed: ' + str(ex), None)


# --------------------------------------------------------------------------
# Vehicle.onHealthChanged: 自車の被弾 (hit_taken) と撃破 (death) を検知
# 第1引数が newHealth。直前HPは self.health を信用せず自前追跡する。
# --------------------------------------------------------------------------

try:
    import Vehicle as _VehicleH

    _orig_onHealthChanged = _VehicleH.Vehicle.onHealthChanged

    def _max_health_of(vehicle):
        """maxHealth 属性を推測で取りに行く。両方失敗すれば None（安全側）。"""
        try:
            v = vehicle.maxHealth
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        except Exception:
            pass
        try:
            v = vehicle.typeDescriptor.maxHealth
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        except Exception:
            pass
        return None

    def _hooked_onHealthChanged(self, *args, **kwargs):
        global _death_recorded, _last_known_health
        try:
            player = BigWorld.player()
            pid = getattr(player, 'playerVehicleID', None)
            if pid is not None and getattr(self, 'id', None) == pid and args:
                new_health = args[0]
                if isinstance(new_health, (int, float)):
                    max_h = _max_health_of(self)
                    # 初回はこの一撃を受ける前は満タンHPだったとみなす
                    prev = _last_known_health if _last_known_health is not None else max_h
                    if prev is not None:
                        damage = prev - new_health
                        if damage > 0:
                            extra = {'damage': damage}
                            if max_h:
                                extra['damage_pct'] = round(damage / max_h, 4)
                            _record('hit_taken', **extra)
                    _last_known_health = new_health
                    if not _death_recorded and new_health <= 0:
                        _death_recorded = True
                        _record('death')
                        BigWorld.logInfo(MOD_NAME, 'player death recorded', None)
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'health hook error: ' + str(ex), None)
        return _orig_onHealthChanged(self, *args, **kwargs)

    _VehicleH.Vehicle.onHealthChanged = _hooked_onHealthChanged
    BigWorld.logInfo(MOD_NAME, 'Vehicle.onHealthChanged hook installed', None)
except Exception as ex:
    BigWorld.logWarning(MOD_NAME, 'health hook failed: ' + str(ex), None)


# --------------------------------------------------------------------------
# PlayerAvatar.onArenaPeriodChange: バトル開始検知（遅延フック）
# personality 読み込み時は Avatar が循環 import 中で PlayerAvatar が
# 未定義のため、ゲームループ開始後にリトライして張る。
# --------------------------------------------------------------------------

def _try_hook_avatar(attempt=0):
    try:
        import Avatar as _Avatar
        cls = _Avatar.PlayerAvatar  # 未初期化なら AttributeError

        orig = cls.onArenaPeriodChange

        def hooked(self, period, *args, **kwargs):
            try:
                if period == 3:  # BATTLE
                    global _arena_start_epoch
                    _arena_start_epoch = round(time.time(), 3)
                    _record('battle_start')
                    BigWorld.logInfo(MOD_NAME, 'battle started', None)
            except Exception as ex:
                BigWorld.logWarning(MOD_NAME, 'period hook error: ' + str(ex), None)
            return orig(self, period, *args, **kwargs)

        cls.onArenaPeriodChange = hooked
        BigWorld.logInfo(MOD_NAME, 'Avatar hook installed (attempt %d)' % attempt, None)
    except Exception:
        if attempt < 150:  # 最大 5 分リトライ
            BigWorld.callback(2.0, lambda: _try_hook_avatar(attempt + 1))
        else:
            BigWorld.logWarning(MOD_NAME, 'Avatar hook gave up', None)


BigWorld.callback(2.0, lambda: _try_hook_avatar())


# --------------------------------------------------------------------------
# 自車位置のサンプリング（v5）: 戦闘中（playerVehicleID がある間）だけ
# 1秒ごとに x, z を記録する。動画側で「クリップ間の長距離移動」を判定し、
# 早送り＋ミニマップ表示のつなぎを入れるのに使う。
# --------------------------------------------------------------------------

def _own_position():
    player = BigWorld.player()
    pid = getattr(player, 'playerVehicleID', None)
    if pid is None:
        return None
    v = BigWorld.entity(pid)
    if v is not None:
        p = v.position
    else:
        p = player.getOwnVehiclePosition()
    return round(p.x, 1), round(p.z, 1)


def _sample_position():
    try:
        pos = _own_position()
        if pos is not None:
            _positions.append([round(time.time(), 3), pos[0], pos[1]])
            _write()
    except Exception:
        pass  # ロビー・ロード中などは取れなくて当然なので黙って次へ
    BigWorld.callback(POSITION_INTERVAL_SEC, _sample_position)


BigWorld.callback(POSITION_INTERVAL_SEC, _sample_position)
BigWorld.logInfo(MOD_NAME, 'v5 position sampler started', None)


# --------------------------------------------------------------------------
# 連射砲（AutoShootGunController）の射撃検知（v6）
# set_stateStatus は射撃状態が変わるたびに呼ばれる。元処理の後に
# 内部状態 __componentState.isShooting を見て連射の開始・終了を判定する。
# --------------------------------------------------------------------------

_auto_bursting = {}  # id(controller) -> [controller, 次に shot を刻む epoch]


def _is_own_controller(ctrl):
    player = BigWorld.player()
    pid = getattr(player, 'playerVehicleID', None)
    entity = getattr(ctrl, 'entity', None)
    return pid is not None and entity is not None and getattr(entity, 'id', None) == pid


def _auto_is_shooting(ctrl):
    state = getattr(ctrl, '_AutoShootGunController__componentState', None)
    v = getattr(state, 'isShooting', False)
    if callable(v):
        v = v()
    return bool(v)


def _auto_update(ctrl):
    key = id(ctrl)
    shooting = _auto_is_shooting(ctrl)
    if shooting and key not in _auto_bursting:
        _record('shot', auto=True)
        _auto_bursting[key] = [ctrl, time.time() + AUTO_SHOT_INTERVAL_SEC]
    elif not shooting and key in _auto_bursting:
        del _auto_bursting[key]
        _record('burst_end')


def _auto_tick():
    """連射が続いている間、AUTO_SHOT_INTERVAL_SEC ごとに shot を刻む。"""
    now = time.time()
    for key, entry in list(_auto_bursting.items()):
        ctrl, next_at = entry
        try:
            if not _auto_is_shooting(ctrl):
                del _auto_bursting[key]
                _record('burst_end')
            elif now >= next_at:
                _record('shot', auto=True)
                entry[1] = now + AUTO_SHOT_INTERVAL_SEC
        except Exception:
            _auto_bursting.pop(key, None)


try:
    import AutoShootGunController as _ASG

    _asg_cls = _ASG.AutoShootGunController
    _orig_set_stateStatus = _asg_cls.set_stateStatus
    _orig_onDiscreteShot = _asg_cls.onDiscreteShot

    def _hooked_set_stateStatus(self, *args, **kwargs):
        result = _orig_set_stateStatus(self, *args, **kwargs)
        try:
            if _is_own_controller(self):
                _auto_update(self)
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'auto-shoot state hook error: ' + str(ex), None)
        return result

    def _hooked_onDiscreteShot(self, *args, **kwargs):
        try:
            if _is_own_controller(self):
                _record('shot', auto=True, discrete=True)
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'auto-shoot discrete hook error: ' + str(ex), None)
        return _orig_onDiscreteShot(self, *args, **kwargs)

    _asg_cls.set_stateStatus = _hooked_set_stateStatus
    _asg_cls.onDiscreteShot = _hooked_onDiscreteShot
    BigWorld.logInfo(MOD_NAME, 'AutoShootGunController hooks installed', None)
except Exception as ex:
    BigWorld.logWarning(MOD_NAME, 'AutoShootGunController hook failed: ' + str(ex), None)


# --------------------------------------------------------------------------
# フリーカメラ自動復帰（v6）
# BattleReplay.isControllingCamera が False = フリーカメラ（左クリックで切替）。
# --------------------------------------------------------------------------

_camera_restore_pending = False  # 直前に復帰を試みた（次の確認で戻っていなければ失敗）
_camera_error_recorded = False   # 監視自体のエラーは 1 回だけ記録する


def _check_camera():
    global _camera_restore_pending
    if not os.path.exists(BATCH_FLAG_PATH):
        return
    import BattleReplay
    rc = BattleReplay.g_replayCtrl
    if rc is None or not getattr(rc, 'isPlaying', False):
        return
    if not getattr(rc, 'isClientReady', True) or getattr(rc, 'isTimeWarpInProgress', False):
        return
    if getattr(rc, 'isControllingCamera', True):
        if _camera_restore_pending:
            _camera_restore_pending = False
            _record('camera_restored')
        return
    if _camera_restore_pending:
        # 前回戻したはずなのにまだフリーカメラ: 復帰できない
        _record('camera_restore_failed')
        _camera_restore_pending = False
        return
    _record('free_camera')
    BigWorld.logInfo(MOD_NAME, 'free camera detected, restoring bound camera', None)
    _camera_restore_pending = True
    rc._BattleReplay__gotoBoundMode()


def _tick():
    global _camera_error_recorded
    try:
        _auto_tick()
    except Exception:
        pass
    try:
        _check_camera()
    except Exception as ex:
        if not _camera_error_recorded:
            _camera_error_recorded = True
            BigWorld.logWarning(MOD_NAME, 'camera check error: ' + str(ex), None)
            _record('camera_check_error', error=str(ex))
    BigWorld.callback(1.0, _tick)


BigWorld.callback(1.0, _tick)
BigWorld.logInfo(MOD_NAME, 'v6 auto-shoot / camera watcher started', None)
