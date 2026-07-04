# -*- coding: utf-8 -*-
# mod bootstrap: BigWorld personality モジュール 'game' の上書きラッパー。
#
# WoT Classic 2.3 クライアントは gui.mods ローダー（addon mod の正規の入口）
# がどこからも呼ばれておらず、mod_*.py を置いても import されない。
# paths.xml で res_mods が最優先なことを利用し、必ず import される
# personality モジュール game を本ファイルで上書きする:
#   1. 元の scripts.pkg 内の game.pyc を読み出して exec（挙動は完全に維持）
#   2. その後に mod_shot_logger を import
#
# 配置先: res_mods/<version>/scripts/client/game.py

import marshal
import traceback
import zipfile

_PKG_PATH = 'C:/Games/World_of_Tanks_ASIA/res/packages/scripts.pkg'
_ORIG_PYC = 'scripts/client/game.pyc'
_ERR_PATH = 'C:/Games/World_of_Tanks_ASIA/mod_bootstrap_error.txt'


def _log_error(stage):
    try:
        with open(_ERR_PATH, 'a') as f:
            f.write('=== %s ===\n%s\n' % (stage, traceback.format_exc()))
    except Exception:
        pass


try:
    _zf = zipfile.ZipFile(_PKG_PATH)
    _data = _zf.read(_ORIG_PYC)
    _zf.close()
    # py2.7 の pyc は 8 バイトヘッダ（magic 4B + mtime 4B）の後に code object
    _code = marshal.loads(_data[8:])
    exec(_code)
except Exception:
    _log_error('exec original game.pyc')
    raise  # 元の game が動かないとクライアントが成立しないので隠さない

try:
    import mod_shot_logger  # res_mods/<ver>/scripts/client/mod_shot_logger.py
except Exception:
    _log_error('import mod_shot_logger')
