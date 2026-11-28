"""
WoT クライアントから GUI 素材を取り出す（リポジトリには入れず、各 PC のクライアントから
その場で取り出して output/cache に置く。素材は Wargaming のものなので配布しない）。

- HP バーの網目模様: damagePanel.swf に埋め込まれた 220×14 の黒い市松模様
  （半透明）。ゲームは色の付いた塗りの上にこれを重ねて網目に見せている
  （2026-11-28 に SWF のビットマップを書き出して確認）
"""

import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np

from src.config import OUTPUT_DIR, wot_dir

CACHE_DIR = OUTPUT_DIR / "cache"
HP_MESH_PATH = CACHE_DIR / "hp_mesh.png"
HP_MESH_SIZE = (220, 14)  # (幅, 高さ)。キャラクター ID はバージョンで変わりうるので大きさで探す
_DAMAGE_PANEL_SWF = "gui/flash/damagePanel.swf"


def swf_lossless_bitmaps(swf: bytes):
    """SWF の DefineBitsLossless2（32bit ARGB）を (幅, 高さ, BGRA 配列) で列挙する。"""
    if swf[:3] == b"CWS":
        body = zlib.decompress(swf[8:])
    elif swf[:3] == b"FWS":
        body = swf[8:]
    else:
        return
    nbits = body[0] >> 3
    pos = (5 + 4 * nbits + 7) // 8 + 4  # RECT + フレームレート + フレーム数
    while pos + 2 <= len(body):
        code_len = struct.unpack_from("<H", body, pos)[0]
        pos += 2
        code, ln = code_len >> 6, code_len & 0x3F
        if ln == 0x3F:
            ln = struct.unpack_from("<I", body, pos)[0]
            pos += 4
        data = body[pos:pos + ln]
        pos += ln
        if code == 0:
            break
        if code != 36 or len(data) < 7:
            continue
        _cid, fmt, w, h = struct.unpack_from("<HBHH", data, 0)
        if fmt != 5:
            continue
        px = np.frombuffer(zlib.decompress(data[7:]), np.uint8)[: w * h * 4].reshape(h, w, 4)
        a = px[..., 0:1].astype(np.float32)
        rgb = np.where(a > 0, px[..., 1:4].astype(np.float32) * 255.0 / np.maximum(a, 1), 0)
        yield w, h, np.concatenate([rgb[..., ::-1], a], axis=2).clip(0, 255).astype(np.uint8)


def ensure_hp_mesh(wot: Path | None = None) -> Path | None:
    """HP バーの網目模様を取り出して PNG で置く（済みならそのパス）。取れなければ None。"""
    if HP_MESH_PATH.exists():
        return HP_MESH_PATH
    import cv2
    wot = Path(wot) if wot is not None else wot_dir()
    for pkg in sorted((wot / "res" / "packages").glob("gui-part*.pkg")):
        try:
            z = zipfile.ZipFile(pkg)
            if _DAMAGE_PANEL_SWF not in z.namelist():
                continue
            swf = z.read(_DAMAGE_PANEL_SWF)
        except (OSError, zipfile.BadZipFile):
            continue
        for w, h, img in swf_lossless_bitmaps(swf):
            if (w, h) == HP_MESH_SIZE:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(HP_MESH_PATH), img)
                return HP_MESH_PATH
    return None
