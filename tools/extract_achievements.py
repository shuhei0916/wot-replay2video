# -*- coding: utf-8 -*-
"""
WoT クライアントから勲章 ID → 日本語名の対応表を抽出して
src/data/achievements.json を生成する開発用ツール。

バトルリザルト（.wotreplay Block 2 の personal.achievements）に入る勲章 ID は
scripts/common/dossiers2/custom/records.pyc の RECORD_DB_IDS（手書きの安定 ID 辞書、
連番インデックスではない）で解決される。records.pyc は Python 2.7 バイトコードなので、
marshal を読む最小リーダーと、辞書リテラルを再構築する最小スタックマシンで抽出する。
日本語表示名は res/text/lc_messages/achievements.mo（gettext）から取る。

使い方（クライアント更新後に再実行して差分をコミットする）:
    python tools/extract_achievements.py
"""

import gettext
import io
import json
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import wot_dir  # noqa: E402

RECORDS_PYC = "scripts/common/dossiers2/custom/records.pyc"
OUT_PATH = Path(__file__).parent.parent / "src" / "data" / "achievements.json"


class _Py2Marshal:
    """Python 2.7 の marshal 形式リーダー（records.pyc に必要な型のみ）。"""

    def __init__(self, data: bytes):
        self.f = io.BytesIO(data)
        self.interned: list[bytes] = []

    def _r(self, n: int) -> bytes:
        return self.f.read(n)

    def _i32(self) -> int:
        return struct.unpack("<i", self._r(4))[0]

    def load(self):
        c = self._r(1).decode("latin1")
        if c == "N": return None
        if c == "F": return False
        if c == "T": return True
        if c == "i": return self._i32()
        if c == "I": return struct.unpack("<q", self._r(8))[0]
        if c == "g": return struct.unpack("<d", self._r(8))[0]
        if c == "l":
            n = self._i32()
            digits = [struct.unpack("<H", self._r(2))[0] for _ in range(abs(n))]
            val = 0
            for d in reversed(digits):
                val = val * 32768 + d
            return -val if n < 0 else val
        if c == "s":
            return self._r(self._i32())
        if c == "t":
            s = self._r(self._i32())
            self.interned.append(s)
            return s
        if c == "R":
            return self.interned[self._i32()]
        if c == "u":
            return self._r(self._i32()).decode("utf-8", "replace")
        if c == "(":
            n = self._i32()
            return tuple(self.load() for _ in range(n))
        if c == "[":
            n = self._i32()
            return [self.load() for _ in range(n)]
        if c == "c":
            keys = ("argcount", "nlocals", "stacksize", "flags")
            co = {k: self._i32() for k in keys}
            for k in ("code", "consts", "names", "varnames", "freevars",
                      "cellvars", "filename", "name"):
                co[k] = self.load()
            co["firstlineno"] = self._i32()
            co["lnotab"] = self.load()
            return _Code(co)
        raise ValueError(f"未対応の marshal 型 {c!r} (offset {self.f.tell()})")


class _Code:
    def __init__(self, d: dict):
        self.d = d


# Python 2.7 オペコード（必要分のみ）
_STORE_MAP = 54
_GET_ITER = 68
_STORE_NAME = 90
_LOAD_CONST = 100
_LOAD_NAME = 101
_BUILD_TUPLE = 102
_BUILD_LIST = 103
_BUILD_MAP = 105
_CALL_FUNCTION = 131
_MAKE_FUNCTION = 132
_EXTENDED_ARG = 145
_HAVE_ARGUMENT = 90


def _eval_module_store(code_obj: _Code, target: bytes):
    """
    モジュールバイトコードをリテラル構築の範囲で擬似実行し、
    `target = <リテラル>` で束縛された値を返す。

    関数呼び出し等は評価せずマーカーを積む（target の構築がリテラルで
    完結している限り正しい値が得られる）。
    """
    code: bytes = code_obj.d["code"]
    consts = code_obj.d["consts"]
    names = code_obj.d["names"]

    stack: list = []
    i, ext = 0, 0
    while i < len(code):
        op = code[i]
        i += 1
        arg = None
        if op >= _HAVE_ARGUMENT:
            arg = code[i] | (code[i + 1] << 8) | ext
            ext = 0
            i += 2

        if op == _EXTENDED_ARG:
            ext = arg << 16
        elif op == _LOAD_CONST:
            stack.append(consts[arg])
        elif op == _LOAD_NAME:
            stack.append(("<name>", names[arg]))
        elif op == _BUILD_TUPLE:
            items = stack[len(stack) - arg:]
            del stack[len(stack) - arg:]
            stack.append(tuple(items))
        elif op == _BUILD_LIST:
            items = stack[len(stack) - arg:]
            del stack[len(stack) - arg:]
            stack.append(items)
        elif op == _BUILD_MAP:
            stack.append({})
        elif op == _STORE_MAP:
            key = stack.pop()
            value = stack.pop()
            stack[-1][key] = value
        elif op == _GET_ITER:
            pass
        elif op == _MAKE_FUNCTION:
            del stack[len(stack) - arg - 1:]
            stack.append(("<func>",))
        elif op == _CALL_FUNCTION:
            npos, nkw = arg & 0xFF, (arg >> 8) & 0xFF
            del stack[len(stack) - npos - 2 * nkw - 1:]
            stack.append(("<call>",))
        elif op == _STORE_NAME:
            val = stack.pop() if stack else None
            if names[arg] == target:
                return val
        else:
            raise ValueError(f"未対応 opcode {op} (offset {i - 3})")
    raise ValueError(f"{target!r} が見つかりません")


def extract_record_db_ids(wot: Path) -> dict[int, tuple[str, str]]:
    """クライアントの records.pyc から DB ID → (block, record) を返す。"""
    pkg = wot / "res" / "packages" / "scripts.pkg"
    with zipfile.ZipFile(pkg) as z:
        raw = z.read(RECORDS_PYC)
    module = _Py2Marshal(raw[8:]).load()  # 先頭8B: magic + mtime
    db = _eval_module_store(module, b"RECORD_DB_IDS")
    out = {}
    for k, v in db.items():
        if isinstance(k, tuple) and len(k) == 2 and isinstance(v, int):
            out[v] = (k[0].decode(), k[1].decode())
    return out


def load_ja_names(wot: Path) -> dict[str, str]:
    """achievements.mo から 内部名 → 日本語表示名を返す。"""
    mo = wot / "res" / "text" / "lc_messages" / "achievements.mo"
    with open(mo, "rb") as f:
        catalog = gettext.GNUTranslations(f)._catalog
    return {k: v for k, v in catalog.items() if isinstance(k, str) and v}


def main() -> None:
    wot = wot_dir()
    db_ids = extract_record_db_ids(wot)
    ja = load_ja_names(wot)

    achievements = {}
    for db_id, (block, record) in sorted(db_ids.items()):
        if block != "achievements":
            continue  # steamAchievements 等のミラー実績はタイトルには不要
        name_ja = ja.get(record)
        achievements[str(db_id)] = {"name": record, **({"ja": name_ja} if name_ja else {})}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"achievements": achievements}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"{len(achievements)} 件を書き出しました → {OUT_PATH}")


if __name__ == "__main__":
    main()
