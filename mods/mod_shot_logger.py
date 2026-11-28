# -*- coding: utf-8 -*-
# WoT の組み込み Python は 2.7。py2.7 でコンパイルした .pyc を
# res_mods/<version>/scripts/client/ に配置する（ソース .py は無視される）。
"""
mod_shot_logger v10 - リプレイ再生中の射撃・被弾・与ダメージ・戦況・戦果を shot_events.json に記録する。

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
- 与ダメージ・命中の記録（v7、2026-11-27）: ハイライトを「当たった射撃」で選べるように。
  クライアント 2.4.2.0 の引数（scripts.pkg の co_varnames で確認）:
    Vehicle.onHealthChanged(newHealth, oldHealth, attackerID, attackReasonID, attackReasonExtID)
    Vehicle.showDamageFromShot(attackerID, hitPoints, effectsIndex, prefabEffIndex,
                               damage, damageFactor, lastMaterialIsShield, ...)
  - damage_dealt: 自分の攻撃で敵の HP が減った（target, damage, killed, reason）
  - hit_dealt: 自分の弾が敵に当たった（damage_factor=0 なら非貫通・跳弾）
  2026-11-27 実機確認: Chi-Go の1戦で shot 13 / hit_dealt 11（うち非貫通2）/ damage_dealt
  合計 2548・撃破2 が戦闘結果と完全一致。attackReasonID は 0=射撃、2=体当たり。
  戦果リボン（PlayerAvatar.onBattleEvents）も狙ったが、Avatar の遅延フック自体が
  張れない（既存の onArenaPeriodChange と同じく gave up）ため不採用
- 戦況の記録（v8、2026-11-28。見どころ曲線の「状況の高さ」・勲章の時刻特定・HPバーの
  描き直し・意味のある移動の判定に使う）:
  - hit_taken に attacker / reason / health / max_health を追加（自車 HP の推移と、誰に撃たれたか）
  - hit_received: 自車に敵の弾が当たった（damage_factor=0 なら装甲で弾いた）
  - damage_dealt に target_team / target_tier / target_class を追加（勲章の Tier・車種条件用）
  - arena_info: 試合開始時に1回、全車両の team / tier / class / name と自チーム
  - vehicle_killed: 全車両の撃破（キルログ。arena.onVehicleKilled）。残り台数の推移が分かる
  - others: 自車以外で見えている車両の位置（2秒ごと [epoch, vid, x, z]）
- リプレイ再生中だけ記録する（v9、2026-11-28）: mod は personality モジュールの差し替えで
  WoT 起動のたびに読み込まれるため、普段のプレイ中も記録し続けていた。記録は試合をまたいで
  溜まり、毎秒ファイル全体を書き直すので、続けて遊ぶとカクつきの原因になりえた。
  BattleReplay.g_replayCtrl.isPlaying が真のときだけ記録し、普段のプレイでは何もしない。
  あわせて2試合目が始まったら記録をリセットする
- プレイヤー側（PlayerAvatar）のフック（v10、2026-11-28）: 以前は onArenaPeriodChange を先に
  張ろうとして、2.4.2.0 では内部名（__onArenaPeriodChange）に変わっていたため毎回失敗し、後続も
  張れていなかった（game.log の "Avatar hook gave up"）。メソッドごとに独立して張る:
  - battle_event: onBattleEvents（画面の戦果リボンの元データ）。種類名は BattleFeedbackCommon の
    BATTLE_EVENT_TYPE から引き、details は unpackDamage / unpackCrits で解いて記録する
    （アシスト・防いだダメージ量・クリティカル・発見など）
  - battle_summary: battleEventsSummary（与ダメ・履帯／偵察／スタンのアシスト・防いだダメージの累計）
  - module_damage: showVehicleDamageInfo のうち自車のもの（モジュール破損・乗員負傷・修理）
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
_others = []     # [[epoch, vid, x, z], ...] 自車以外で見えている車両の位置（v8、2秒ごと）
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
                   'positions': _positions,
                   'others': _others}, f)
        f.close()
    except Exception:
        pass


def _replay_playing():
    """リプレイ再生中か（普段のプレイ中は録画側の g_replayCtrl はあっても isPlaying は偽）。"""
    try:
        import BattleReplay
        rc = BattleReplay.g_replayCtrl
        return rc is not None and bool(getattr(rc, 'isPlaying', False))
    except Exception:
        return False


def _reset_log():
    """2試合目以降の開始時に、前の試合の記録を捨てる（v9）。"""
    global _death_recorded, _last_known_health
    del _events[:]
    del _positions[:]
    del _others[:]
    _death_recorded = False
    _last_known_health = None
    try:
        _vehicle_info_cache.clear()
    except NameError:
        pass


def _record(kind, **extra):
    if not _replay_playing():
        return  # 普段のプレイでは記録しない（v9）
    ev = {'epoch': round(time.time(), 3), 'type': kind}
    ev.update(extra)
    _events.append(ev)
    _write()


import BigWorld  # noqa: E402

_write()  # ロード確認用の初期書き出し
BigWorld.logInfo(MOD_NAME, 'v10 loaded. output -> ' + OUTPUT_PATH, None)


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
# 車両情報（v8）: arena.vehicles から team / tier / class / name を引く（キャッシュ）
# --------------------------------------------------------------------------

_CLASS_TAGS = ('lightTank', 'mediumTank', 'heavyTank', 'AT-SPG', 'SPG')
_vehicle_info_cache = {}


def _vehicle_info(vid):
    """{'team', 'tier', 'class', 'name'}。取れない項目は入れない。"""
    if vid in _vehicle_info_cache:
        return _vehicle_info_cache[vid]
    info = {}
    try:
        v = BigWorld.player().arena.vehicles.get(vid) or {}
        if 'team' in v:
            info['team'] = v['team']
        if v.get('name'):
            info['name'] = v['name']
        vt = v.get('vehicleType')
        if vt is not None:
            info['tier'] = vt.type.level
            tags = vt.type.tags
            for c in _CLASS_TAGS:
                if c in tags:
                    info['class'] = c
                    break
            info['tank'] = vt.type.name
    except Exception:
        pass
    if info:
        _vehicle_info_cache[vid] = info
    return info


def _target_extra(vid):
    info = _vehicle_info(vid)
    out = {}
    for k in ('team', 'tier', 'class'):
        if k in info:
            out['target_' + k] = info[k]
    return out


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
            if (pid is not None and getattr(self, 'id', None) != pid
                    and len(args) >= 3 and args[2] == pid):
                # 自分の攻撃で敵の HP が減った（v7）
                new_h, old_h = args[0], args[1]
                if isinstance(new_h, (int, float)) and isinstance(old_h, (int, float)):
                    dmg = old_h - max(new_h, 0)
                    if dmg > 0:
                        extra = {'target': self.id, 'damage': dmg, 'killed': new_h <= 0}
                        if len(args) >= 4:
                            extra['reason'] = args[3]
                        extra.update(_target_extra(self.id))
                        _record('damage_dealt', **extra)
            if pid is not None and getattr(self, 'id', None) == pid and args:
                new_health = args[0]
                if isinstance(new_health, (int, float)):
                    max_h = _max_health_of(self)
                    # 初回はこの一撃を受ける前は満タンHPだったとみなす
                    prev = _last_known_health if _last_known_health is not None else max_h
                    if prev is not None:
                        damage = prev - new_health
                        if damage > 0:
                            extra = {'damage': damage, 'health': new_health}
                            if max_h:
                                extra['damage_pct'] = round(damage / max_h, 4)
                                extra['max_health'] = max_h
                            if len(args) >= 3:
                                extra['attacker'] = args[2]
                            if len(args) >= 4:
                                extra['reason'] = args[3]
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
# Vehicle.showDamageFromShot（v7）: 自分の弾が敵に当たった（非貫通・跳弾も含む）
# --------------------------------------------------------------------------

try:
    import Vehicle as _VehicleS

    _orig_showDamageFromShot = _VehicleS.Vehicle.showDamageFromShot

    def _hooked_showDamageFromShot(self, *args, **kwargs):
        try:
            pid = getattr(BigWorld.player(), 'playerVehicleID', None)
            if pid is not None and args and args[0] == pid and getattr(self, 'id', None) != pid:
                extra = {'target': self.id}
                if len(args) >= 6:
                    extra['damage'] = args[4]
                    extra['damage_factor'] = args[5]
                _record('hit_dealt', **extra)
            elif (pid is not None and args and getattr(self, 'id', None) == pid
                  and args[0] != pid):
                # 自車に敵の弾が当たった（v8）。damage_factor=0 なら装甲で弾いた
                extra = {'attacker': args[0]}
                if len(args) >= 6:
                    extra['damage'] = args[4]
                    extra['damage_factor'] = args[5]
                _record('hit_received', **extra)
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'shot-damage hook error: ' + str(ex), None)
        return _orig_showDamageFromShot(self, *args, **kwargs)

    _VehicleS.Vehicle.showDamageFromShot = _hooked_showDamageFromShot
    BigWorld.logInfo(MOD_NAME, 'Vehicle.showDamageFromShot hook installed', None)
except Exception as ex:
    BigWorld.logWarning(MOD_NAME, 'shot-damage hook failed: ' + str(ex), None)


# --------------------------------------------------------------------------
# PlayerAvatar.onArenaPeriodChange: バトル開始検知（遅延フック）
# personality 読み込み時は Avatar が循環 import 中で PlayerAvatar が
# 未定義のため、ゲームループ開始後にリトライして張る。
# --------------------------------------------------------------------------

_BATTLE_EVENT_NAMES = None  # {値: 名前}（BattleFeedbackCommon.BATTLE_EVENT_TYPE から作る）


def _battle_event_names():
    global _BATTLE_EVENT_NAMES
    if _BATTLE_EVENT_NAMES is None:
        names = {}
        try:
            from BattleFeedbackCommon import BATTLE_EVENT_TYPE as BET
            for k in dir(BET):
                v = getattr(BET, k)
                if k.isupper() and isinstance(v, int):
                    names[v] = k
        except Exception:
            pass
        _BATTLE_EVENT_NAMES = names
    return _BATTLE_EVENT_NAMES


def _simple(v):
    """JSON にできる単純な値に変換する（タプルはリストに、それ以外は文字列に）。"""
    if isinstance(v, (int, long, float, bool)) or v is None:
        return v
    if isinstance(v, (tuple, list)):
        return [_simple(x) for x in v]
    return str(v)


def _unpack_details(name, details):
    """戦果イベントの details を解く。失敗したら元の値のまま。"""
    try:
        from BattleFeedbackCommon import BATTLE_EVENT_TYPE as BET
        if name in ('DAMAGE', 'RECEIVED_DAMAGE', 'TANKING', 'RADIO_ASSIST', 'TRACK_ASSIST',
                    'STUN_ASSIST', 'SMOKE_ASSIST', 'INSPIRE_ASSIST'):
            return _simple(BET.unpackDamage(details))
        if name in ('CRIT', 'RECEIVED_CRIT'):
            return _simple(BET.unpackCrits(details))
    except Exception:
        pass
    return _simple(details)


def _hook_avatar_method(cls, attr, wrap):
    """cls.attr を wrap(orig) で置き換える。無ければ False（他のフックには影響しない）。"""
    orig = getattr(cls, attr, None)
    if orig is None:
        return False
    setattr(cls, attr, wrap(orig))
    BigWorld.logInfo(MOD_NAME, 'PlayerAvatar.%s hook installed' % attr, None)
    return True


def _wrap_battle_events(orig):
    def hooked(self, events, *args, **kwargs):
        try:
            names = _battle_event_names()
            for ev in events or []:
                get = ev.get if hasattr(ev, 'get') else (lambda k, d=None: getattr(ev, k, d))
                etype = get('eventType')
                name = names.get(etype, str(etype))
                _record('battle_event', etype=etype, name=name, target=get('targetID'),
                        count=_simple(get('count')), details=_unpack_details(name, get('details')))
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'battle events hook error: ' + str(ex), None)
        return orig(self, events, *args, **kwargs)
    return hooked


def _wrap_battle_summary(orig):
    def hooked(self, summary, *args, **kwargs):
        try:
            data = dict((str(k), _simple(v)) for k, v in dict(summary).items())
            _record('battle_summary', **data)
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'battle summary hook error: ' + str(ex), None)
        return orig(self, summary, *args, **kwargs)
    return hooked


def _wrap_damage_info(orig):
    def hooked(self, vehicleID, damageIndex, extraIndex, *args, **kwargs):
        try:
            if vehicleID == getattr(self, 'playerVehicleID', None):
                code, device = damageIndex, None
                try:
                    import constants
                    code = constants.DAMAGE_INFO_CODES[damageIndex]
                except Exception:
                    pass
                try:
                    if extraIndex:
                        vt = self.arena.vehicles[vehicleID]['vehicleType']
                        device = vt.extras[extraIndex].name
                except Exception:
                    pass
                _record('module_damage', code=_simple(code), device=_simple(device),
                        attacker=_simple(args[0]) if args else None)
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'damage info hook error: ' + str(ex), None)
        return orig(self, vehicleID, damageIndex, extraIndex, *args, **kwargs)
    return hooked


def _try_hook_avatar(attempt=0):
    try:
        import Avatar as _Avatar
        cls = _Avatar.PlayerAvatar  # 未初期化なら AttributeError
    except Exception:
        if attempt < 150:  # 最大 5 分リトライ
            BigWorld.callback(2.0, lambda: _try_hook_avatar(attempt + 1))
        else:
            BigWorld.logWarning(MOD_NAME, 'Avatar hook gave up (class not found)', None)
        return
    for attr, wrap in (('onBattleEvents', _wrap_battle_events),
                       ('battleEventsSummary', _wrap_battle_summary),
                       ('showVehicleDamageInfo', _wrap_damage_info)):
        try:
            if not _hook_avatar_method(cls, attr, wrap):
                BigWorld.logWarning(MOD_NAME, 'PlayerAvatar.%s not found' % attr, None)
        except Exception as ex:
            BigWorld.logWarning(MOD_NAME, 'PlayerAvatar.%s hook failed: %s' % (attr, ex), None)


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
        pos = _own_position() if _replay_playing() else None
        if pos is not None:
            _positions.append([round(time.time(), 3), pos[0], pos[1]])
            _write()
    except Exception:
        pass  # ロビー・ロード中などは取れなくて当然なので黙って次へ
    BigWorld.callback(POSITION_INTERVAL_SEC, _sample_position)


BigWorld.callback(POSITION_INTERVAL_SEC, _sample_position)
BigWorld.logInfo(MOD_NAME, 'v5 position sampler started', None)


# --------------------------------------------------------------------------
# 戦況の監視（v8）: 試合の車両一覧、全車両の撃破（キルログ）、見えている車両の位置
# --------------------------------------------------------------------------

OTHERS_INTERVAL_SEC = 2.0
_arena_hooked = None   # キルログを購読した arena（試合ごとに1回）


def _on_vehicle_killed(*args):
    # ClientArena.updateVehicleIsAlive が onVehicleKilled(victimID, killerID, equipmentID,
    # reasonID, numVehiclesAffected) で発火する（2.4.2.0 の逆アセンブルで確認）。
    # 引数が変わっても例外を出さないよう *args で受ける
    try:
        if not args:
            return
        target_id = args[0]
        extra = {'target': target_id}
        if len(args) >= 2:
            extra['attacker'] = args[1]
        if len(args) >= 4:
            extra['reason'] = args[3]
        extra.update(_target_extra(target_id))
        _record('vehicle_killed', **extra)
    except Exception as ex:
        BigWorld.logWarning(MOD_NAME, 'kill feed error: ' + str(ex), None)


def _watch_arena():
    global _arena_hooked
    try:
        player = BigWorld.player()
        arena = getattr(player, 'arena', None)
        pid = getattr(player, 'playerVehicleID', None)
        if arena is not None and pid is not None and _replay_playing():
            if _arena_hooked is not arena:
                if _arena_hooked is not None:
                    _reset_log()   # 2試合目以降（1試合目の記録はロード中のものも残す）
                _arena_hooked = arena
                arena.onVehicleKilled += _on_vehicle_killed
                vehicles = {}
                for vid in arena.vehicles.keys():
                    vehicles[str(vid)] = _vehicle_info(vid)
                _record('arena_info', player_vehicle=pid,
                        player_team=_vehicle_info(pid).get('team'), vehicles=vehicles)
                BigWorld.logInfo(MOD_NAME, 'arena watch started (%d vehicles)' % len(vehicles), None)
            now = round(time.time(), 3)
            for vid in arena.vehicles.keys():
                if vid == pid:
                    continue
                v = BigWorld.entity(vid)
                if v is None or not getattr(v, 'isAlive', lambda: False)():
                    continue
                _others.append([now, vid, round(v.position.x, 1), round(v.position.z, 1)])
    except Exception:
        pass  # ロビー・ロード中などは取れなくて当然
    BigWorld.callback(OTHERS_INTERVAL_SEC, _watch_arena)


BigWorld.callback(OTHERS_INTERVAL_SEC, _watch_arena)


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
_last_camera_state = None        # 診断用: 前回記録したカメラ状態
_camera_fail_ticks = 0           # 演出中以外で戻せなかった連続秒数
CAMERA_FAIL_TICKS = 5            # これだけ続けて戻せなければ失敗とする
PREBATTLE_HIGHLIGHTS_MODE = 'prebattleHighlights'  # CTRL_MODE_NAME.PREBATTLE_HIGHLIGHTS


def _record_camera_state(rc):
    """診断用: リプレイ制御とカメラの状態が変わったときだけ camera_state を記録する。"""
    global _last_camera_state
    mode = None
    try:
        mode = BigWorld.player().inputHandler.ctrlModeName
    except Exception:
        pass
    recorded = None
    try:
        recorded = rc.getRecordedControlMode()
    except Exception:
        pass
    state = (bool(getattr(rc, 'isPlaying', None)),
             bool(getattr(rc, 'isClientReady', None)),
             bool(getattr(rc, 'isTimeWarpInProgress', None)),
             getattr(rc, 'isControllingCamera', None),
             mode, recorded)
    # スナイパー⇔アーケードの切替（mode）は頻繁なので、変化の判定からは外す
    key = state[:4] + (state[5],)
    if key != _last_camera_state:
        _last_camera_state = key
        _record('camera_state', playing=state[0], ready=state[1], warp=state[2],
                bound=state[3], mode=state[4], recorded=state[5])


def _check_camera():
    global _camera_restore_pending, _camera_fail_ticks
    if not os.path.exists(BATCH_FLAG_PATH):
        return
    import BattleReplay
    rc = BattleReplay.g_replayCtrl
    if rc is not None:
        _record_camera_state(rc)
    if rc is None or not getattr(rc, 'isPlaying', False):
        return
    if not getattr(rc, 'isClientReady', True) or getattr(rc, 'isTimeWarpInProgress', False):
        return
    if getattr(rc, 'isControllingCamera', True):
        if _camera_restore_pending:
            _camera_restore_pending = False
            _camera_fail_ticks = 0
            _record('camera_restored')
        return
    if not _camera_restore_pending:
        _camera_restore_pending = True
        _camera_fail_ticks = 0
        _record('free_camera')
        BigWorld.logInfo(MOD_NAME, 'free camera detected, restoring bound camera', None)
    # 録画者側で開幕ハイライト演出 (prebattleHighlights) 中は、ゲーム自身が
    # onControlModeChanged で追従を外すので戻せない。演出が終わるまで待ち、
    # 失敗には数えない（2026-11-23 SuperPershing で確認）。__gotoBoundMode は
    # 呼ぶたびに画面へ通知を出すので、演出中は呼ばない
    try:
        recorded = rc.getRecordedControlMode()
    except Exception:
        recorded = None
    if recorded == PREBATTLE_HIGHLIGHTS_MODE:
        return
    _camera_fail_ticks += 1
    if _camera_fail_ticks == CAMERA_FAIL_TICKS:
        _record('camera_restore_failed', recorded=recorded)
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
BigWorld.logInfo(MOD_NAME, 'v10 auto-shoot / camera watcher started', None)
