# -*- coding: utf-8 -*-
# WoT の組み込み Python は 2.7。py2.7 でコンパイルした .pyc を
# res_mods/<version>/scripts/client/ に配置する（ソース .py は無視される）。
"""
mod_shot_logger v4 - リプレイ再生中の射撃・被弾イベントを shot_events.json に記録する。

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
"""

import json
import time

MOD_NAME = 'mod_shot_logger'
OUTPUT_PATH = 'C:/Games/World_of_Tanks_ASIA/shot_events.json'

_events = []
_arena_start_epoch = None
_death_recorded = False
_last_known_health = None  # 自前追跡する直前HP（onHealthChanged 間で引き継ぐ）


def _write():
    try:
        f = open(OUTPUT_PATH, 'w')
        json.dump({'arena_start_epoch': _arena_start_epoch,
                   'events': _events}, f)
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
BigWorld.logInfo(MOD_NAME, 'v4 loaded. output -> ' + OUTPUT_PATH, None)


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
