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


# ---------------------------------------------------------------------------
# HP バーの部品一式（ゲームと同じ組み立て。2026-11-28）
#   ① 枠: 戦闘画面のアトラス（battleAtlas）の DamagePanel_bg 上部、行19〜37・列1〜227
#   ② 塗り: プログラムで描く（src.hp_bar）
#   ③ 網目: damagePanel.swf の 220×14（上の ensure_hp_mesh）
#   ④ 数字: クライアント同梱の Warhelios-Regular.ttf（字幅がゲームの数字と一致）
# 枠と網目はゲームの画素のまま整数倍（HP_SCALE）に最近傍で拡大して置く。
# ---------------------------------------------------------------------------

HP_SCALE = 4
HP_FRAME_CROP = (1, 19, 227, 19)   # DamagePanel_bg 内の (x, y, 幅, 高さ)
HP_FONT_MEMBER = "gui/gameface/fonts/Warhelios-Regular.ttf"   # 字幅がゲームの HP の数字と一致


def _find_member(wot: Path, member: str) -> bytes | None:
    for pkg in sorted((wot / "res" / "packages").glob("gui-part*.pkg")):
        try:
            z = zipfile.ZipFile(pkg)
            if member in z.namelist():
                return z.read(member)
        except (OSError, zipfile.BadZipFile):
            continue
    return None


def _atlas_rect(xml: bytes, name: str) -> tuple[int, int, int, int] | None:
    import re
    text = xml.decode("utf-8", errors="replace")
    for block in re.findall(r"<SubTexture>(.*?)</SubTexture>", text, re.S):
        m = re.search(r"<name>\s*(.*?)\s*</name>", block)
        if m and m.group(1) == name:
            vals = [int(re.search(rf"<{k}>\s*(\d+)\s*</{k}>", block).group(1))
                    for k in ("x", "y", "width", "height")]
            return tuple(vals)
    return None


def ensure_hp_assets(wot: Path | None = None) -> dict | None:
    """
    HP バーの部品（4倍の枠 PNG・4倍の網目 PNG・フォント TTF）を output/cache/<クライアント版>/
    に用意してパスの辞書を返す。どれか取れなければ None（呼び出し側は簡易描画にする）。
    """
    import subprocess

    import cv2

    from src.config import find_ffmpeg, wot_client_version
    wot = Path(wot) if wot is not None else wot_dir()
    try:
        version = wot_client_version()
    except Exception:
        version = "unknown"
    d = CACHE_DIR / version
    paths = {"frame": d / "hp_frame_x4.png", "mesh": d / "hp_mesh_x4.png",
             "font": d / "Warhelios-Regular.ttf", "scale": HP_SCALE}
    if all(Path(paths[k]).exists() for k in ("frame", "mesh", "font")):
        return paths
    d.mkdir(parents=True, exist_ok=True)

    mesh = ensure_hp_mesh(wot)
    font = _find_member(wot, HP_FONT_MEMBER)
    xml = _find_member(wot, "gui/flash/atlases/battleAtlas.xml")
    dds = _find_member(wot, "gui/flash/atlases/battleAtlas.dds")
    if mesh is None or font is None or xml is None or dds is None:
        return None
    rect = _atlas_rect(xml, "DamagePanel_bg")
    ffmpeg = find_ffmpeg()
    if rect is None or ffmpeg is None:
        return None

    paths["font"].write_bytes(font)
    dds_path = d / "battleAtlas.dds"
    dds_path.write_bytes(dds)
    fx, fy, fw, fh = HP_FRAME_CROP
    frame_1x = d / "hp_frame.png"
    r = subprocess.run([ffmpeg, "-v", "error", "-i", str(dds_path), "-vf",
                        f"crop={fw}:{fh}:{rect[0] + fx}:{rect[1] + fy}", "-y", str(frame_1x)],
                       capture_output=True)
    dds_path.unlink(missing_ok=True)   # 14MB あるので残さない
    if r.returncode != 0 or not frame_1x.exists():
        return None
    k = HP_SCALE
    for src, dst in ((frame_1x, paths["frame"]), (mesh, paths["mesh"])):
        img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
        cv2.imwrite(str(dst), cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST))
    return paths


# ---------------------------------------------------------------------------
# 戦闘画面のアイコン（累計表示用。2026-11-28）: battleAtlas から切り出して大きさを揃える
#   damageDealt（与ダメ: 交差した矢印）/ damageBlockedByArmor（防いだダメ: 盾）/
#   ribbonsAssistSpot（アシスト: 偵察の電波）
# ---------------------------------------------------------------------------

ICON_NAMES = {"damage": "damageDealt", "blocked": "damageBlockedByArmor", "assist": "ribbonsAssistSpot"}


def _trim_alpha(img, threshold: int = 12):
    """透明な余白を切り詰める（アルファが threshold を超える範囲の外接矩形）。"""
    if img.ndim != 3 or img.shape[2] != 4:
        return img
    ys, xs = np.where(img[..., 3] > threshold)
    if len(xs) == 0:
        return img
    return img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _fit_square(img, size: int):
    """縦横比を保って size×size の透明な正方形の中央に収める。"""
    import cv2
    h, w = img.shape[:2]
    k = size / max(h, w)
    nw, nh = max(1, round(w * k)), max(1, round(h * k))
    small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    out = np.zeros((size, size, img.shape[2]), np.uint8)
    y0, x0 = (size - nh) // 2, (size - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = small
    return out


def ensure_battle_icons(size: int = 56, wot: Path | None = None) -> dict | None:
    """累計表示のアイコン PNG（size×size、透過）を output/cache/<版>/ に用意して {種類: パス}。"""
    import subprocess

    import cv2

    from src.config import find_ffmpeg, wot_client_version
    wot = Path(wot) if wot is not None else wot_dir()
    try:
        version = wot_client_version()
    except Exception:
        version = "unknown"
    d = CACHE_DIR / version
    paths = {k: d / f"icon_{k}_{size}.png" for k in ICON_NAMES}
    if all(p.exists() for p in paths.values()):
        return paths
    d.mkdir(parents=True, exist_ok=True)
    xml = _find_member(wot, "gui/flash/atlases/battleAtlas.xml")
    dds = _find_member(wot, "gui/flash/atlases/battleAtlas.dds")
    ffmpeg = find_ffmpeg()
    if xml is None or dds is None or ffmpeg is None:
        return None
    dds_path = d / "battleAtlas.dds"
    dds_path.write_bytes(dds)
    try:
        for kind, name in ICON_NAMES.items():
            rect = _atlas_rect(xml, name)
            if rect is None:
                return None
            raw = d / f"icon_{kind}_raw.png"
            r = subprocess.run([ffmpeg, "-v", "error", "-i", str(dds_path), "-vf",
                                f"crop={rect[2]}:{rect[3]}:{rect[0]}:{rect[1]}", "-y", str(raw)],
                               capture_output=True)
            if r.returncode != 0:
                return None
            img = cv2.imread(str(raw), cv2.IMREAD_UNCHANGED)
            img = _trim_alpha(img)  # 元画像は透明な余白が広く、そのまま縮めると小さく見える
            cv2.imwrite(str(paths[kind]), _fit_square(img, size))
            raw.unlink(missing_ok=True)
    finally:
        dds_path.unlink(missing_ok=True)
    return paths


# ---------------------------------------------------------------------------
# 累計が増えたときの光（2026-11-28）: battleDamageLogPanel.swf に1枚だけ入っている 126×40 の
# 柔らかい光の楕円。ゲームは値が変わると数字の後ろにこれを出し、約0.4秒で消す
# （録画の左下 UI を 0.1 秒ごとに見て確認）。フェードは段階ごとの不透明度の PNG で表す。
# ---------------------------------------------------------------------------

GLOW_SIZE = (126, 40)
GLOW_LEVELS = (1.0, 0.7, 0.4, 0.15)   # 0.1 秒ごとの不透明度
_DAMAGE_LOG_SWF = "gui/flash/battleDamageLogPanel.swf"


def ensure_totals_glow(scale: float = 2.0, wot: Path | None = None) -> list[Path] | None:
    """光の PNG を GLOW_LEVELS の段階ぶん output/cache/<版>/ に用意してパスのリストを返す。"""
    import cv2

    from src.config import wot_client_version
    wot = Path(wot) if wot is not None else wot_dir()
    try:
        version = wot_client_version()
    except Exception:
        version = "unknown"
    d = CACHE_DIR / version
    paths = [d / f"totals_glow_{i}.png" for i in range(len(GLOW_LEVELS))]
    if all(p.exists() for p in paths):
        return paths
    swf = _find_member(wot, _DAMAGE_LOG_SWF)
    if swf is None:
        return None
    glow = next((img for w, h, img in swf_lossless_bitmaps(swf) if (w, h) == GLOW_SIZE), None)
    if glow is None:
        return None
    d.mkdir(parents=True, exist_ok=True)
    big = cv2.resize(glow, (round(GLOW_SIZE[0] * scale), round(GLOW_SIZE[1] * scale)),
                     interpolation=cv2.INTER_CUBIC)
    for p, level in zip(paths, GLOW_LEVELS):
        img = big.copy()
        img[..., 3] = (img[..., 3].astype(np.float32) * level).clip(0, 255).astype(np.uint8)
        cv2.imwrite(str(p), img)
    return paths
