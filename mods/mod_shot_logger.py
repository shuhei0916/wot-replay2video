# -*- coding: utf-8 -*-
# WoT の組み込み Python は 2.7。py2.7 でコンパイルした .pyc を
# res_mods/<version>/scripts/client/ に配置する（ソース .py は無視される）。
"""
mod_shot_logger v2 - リプレイ再生中の射撃イベントを shot_events.json に記録する。

設計:
- 各イベントに壁時計時刻 (epoch) を記録する。録画開始時刻(パイプライン側が
  持っている)との差分で動画内タイムスタンプに変換できるため、
  ゲーム内時計やバトル開始検出に依存しない。
- Vehicle.showShooting フック: 自車の射撃を検知（import 時に安全に張れる）
- PlayerAvatar.onArenaPeriodChange フック: バトル開始 (period=3) の記録。
  personality 読み込み時点では Avatar モジュールが未初期化のため、
  BigWorld.callback で遅延リトライして張る。
"""

import json
import time

MOD_NAME = 'mod_shot_logger'
OUTPUT_PATH = 'C:/Games/World_of_Tanks_ASIA/shot_events.json'

_events = []
_arena_start_epoch = None


def _write():
    try:
        f = open(OUTPUT_PATH, 'w')
        json.dump({'arena_start_epoch': _arena_start_epoch,
                   'events': _events}, f)
        f.close()
    except Exception:
        pass


def _record(kind):
    _events.append({'epoch': round(time.time(), 3), 'type': kind})
    _write()


import BigWorld  # noqa: E402

_write()  # ロード確認用の初期書き出し
BigWorld.logInfo(MOD_NAME, 'v2 loaded. output -> ' + OUTPUT_PATH, None)


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
