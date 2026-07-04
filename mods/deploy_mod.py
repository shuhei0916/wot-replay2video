"""
mod_shot_logger を py2.7 バイトコードにコンパイルして res_mods に配置する。

使い方:
    python mods/deploy_mod.py

仕組み（重要な前提知識）:
- WoT Classic 2.x のリテールクライアントは gui.mods ローダー（addon mod の
  正規の入口）がどこからも呼ばれておらず、mod_*.py / .wotmod を置いても
  Python モジュールとしては import されない
- さらに importer は .py ソースを無視し .pyc のみ import する
- そのため、(1) personality モジュール game を game_bootstrap.py の
  コンパイル済み .pyc で上書きし、(2) その中から mod_shot_logger を import する
- .pyc は py2.7 形式が必要。ゲーム同梱の win64/python27.dll を
  64bit Python (py -3.8) から ctypes でロードしてコンパイルに使う
"""

import ctypes
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from xml.etree import ElementTree

WOT_DIR = Path(r"C:\Games\World_of_Tanks_ASIA")
PY27_DLL = WOT_DIR / "win64" / "python27.dll"
PKG_LIB = WOT_DIR / "res" / "packages" / "scripts.pkg" / "scripts" / "common" / "Lib"

MODS_DIR = Path(__file__).parent
SOURCES = {
    MODS_DIR / "game_bootstrap.py": "game.pyc",
    MODS_DIR / "mod_shot_logger.py": "mod_shot_logger.pyc",
}


def _client_version() -> str:
    """paths.xml から res_mods のバージョンディレクトリ名を得る。"""
    tree = ElementTree.parse(WOT_DIR / "paths.xml")
    for p in tree.iter("Path"):
        text = (p.text or "").strip()
        if "res_mods" in text:
            return text.rsplit("/", 1)[-1]
    raise RuntimeError("paths.xml から res_mods パスを特定できません")


def _compile_with_game_python(pairs: list[tuple[Path, Path]]) -> None:
    """ゲーム同梱の python27.dll で .py → py2.7 .pyc にコンパイルする。"""
    if struct.calcsize("P") * 8 == 64:
        _compile_in_process(pairs)
    else:
        # 32bit Python からは 64bit DLL をロードできないので py -3.8 に委譲
        args = []
        for src, dst in pairs:
            args += [str(src), str(dst)]
        r = subprocess.run(["py", "-3.8", str(Path(__file__).resolve())] + args)
        if r.returncode != 0:
            raise RuntimeError("py -3.8 でのコンパイルに失敗しました")


def _compile_in_process(pairs: list[tuple[Path, Path]]) -> None:
    dll = ctypes.CDLL(str(PY27_DLL))
    ctypes.c_int.in_dll(dll, "Py_NoSiteFlag").value = 1
    dll.Py_Initialize()
    script = "import sys\nsys.path.insert(0, r'%s')\nimport py_compile\n" % PKG_LIB
    for src, dst in pairs:
        script += "py_compile.compile(r'%s', r'%s', doraise=True)\n" % (src, dst)
    rc = dll.PyRun_SimpleString(script.encode("utf-8"))
    dll.Py_Finalize()
    if rc != 0:
        raise RuntimeError("py2.7 コンパイルに失敗しました（構文エラー等）")


def deploy() -> None:
    version = _client_version()
    dest_dir = WOT_DIR / "res_mods" / version / "scripts" / "client"
    dest_dir.mkdir(parents=True, exist_ok=True)

    pairs = [(src, dest_dir / out) for src, out in SOURCES.items()]
    _compile_with_game_python(pairs)

    for _, dst in pairs:
        if not dst.exists():
            raise RuntimeError(f"生成されていません: {dst}")
        print(f"配置: {dst}")
    print(f"完了（クライアントバージョン: {version}）")


def uninstall() -> None:
    version = _client_version()
    dest_dir = WOT_DIR / "res_mods" / version / "scripts" / "client"
    for out in SOURCES.values():
        p = dest_dir / out
        if p.exists():
            p.unlink()
            print(f"削除: {p}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1].endswith(".py"):
        # py -3.8 委譲呼び出し: deploy_mod.py <src> <dst> [<src> <dst> ...]
        args = sys.argv[1:]
        _compile_in_process([(Path(a), Path(b)) for a, b in zip(args[::2], args[1::2])])
    elif "--uninstall" in sys.argv:
        uninstall()
    else:
        deploy()
