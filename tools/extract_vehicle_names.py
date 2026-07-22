# -*- coding: utf-8 -*-
"""
WoT クライアントから車両タグ → 公式表示名の対応表を抽出して
src/data/vehicle_names.json を生成する開発用ツール。

.wotreplay の playerVehicle（例 'sweden-S29_UDES_14_5'）は内部タグであり、
文字列加工では正しい表示名にならない（WG の車両改名・再編で内部タグと
現行の公式名がズレるため。例: 'china-Ch43_WZ_122_2' は現在「122 TM」）。

正しい表示名は res/text/lc_messages/<nation>_vehicles.mo（gettext）に、
内部タグ（国名コード抜き）をキーとして格納されている。タグの一覧は
res/packages/scripts.pkg の scripts/item_defs/vehicles/<nation>/*.xml の
ファイル名（拡張子抜き）から取得する（components/ 配下はエンジン等の
モジュール定義であり車両タグではないため除外）。

使い方（クライアント更新後に再実行して差分をコミットする）:
    python tools/extract_vehicle_names.py
"""

import gettext
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import wot_dir  # noqa: E402

OUT_PATH = Path(__file__).parent.parent / "src" / "data" / "vehicle_names.json"

# replay の nation プレフィックス → res/packages/scripts.pkg の item_defs ディレクトリ名
# （ここは一致している。gb_vehicles.mo だけ nation 名と .mo ファイル名がズレる）
NATIONS = [
    "china", "czech", "france", "germany", "italy",
    "japan", "poland", "sweden", "uk", "usa", "ussr",
]

# nation → .mo ファイル名（uk だけ内部的に gb と呼ばれる）
NATION_MO_FILE = {n: f"{n}_vehicles.mo" for n in NATIONS}
NATION_MO_FILE["uk"] = "gb_vehicles.mo"


def _vehicle_tags(pkg: zipfile.ZipFile, nation: str) -> list[str]:
    """scripts.pkg 内の item_defs/vehicles/<nation>/*.xml からタグ一覧を得る。"""
    prefix = f"scripts/item_defs/vehicles/{nation}/"
    tags = []
    for name in pkg.namelist():
        if not name.startswith(prefix) or name == prefix:
            continue
        rest = name[len(prefix):]
        # components/ 配下はエンジン・銃・弾薬等のモジュール定義（車両タグではない）
        if "/" in rest or not rest.endswith(".xml"):
            continue
        tags.append(rest[:-4])
    return tags


def _load_catalog(wot: Path, mo_filename: str) -> dict[str, str]:
    mo = wot / "res" / "text" / "lc_messages" / mo_filename
    with open(mo, "rb") as f:
        catalog = gettext.GNUTranslations(f)._catalog
    return {k: v for k, v in catalog.items() if isinstance(k, str) and v}


def extract_vehicle_names(wot: Path) -> dict[str, str]:
    """{'<nation>-<tag>': '公式表示名'} の辞書を返す。"""
    pkg_path = wot / "res" / "packages" / "scripts.pkg"
    result: dict[str, str] = {}
    with zipfile.ZipFile(pkg_path) as pkg:
        for nation in NATIONS:
            catalog = _load_catalog(wot, NATION_MO_FILE[nation])
            for tag in _vehicle_tags(pkg, nation):
                name = catalog.get(tag)
                if name:
                    result[f"{nation}-{tag}"] = name
    return result


def main() -> None:
    wot = wot_dir()
    vehicles = extract_vehicle_names(wot)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"vehicles": vehicles}, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    print(f"{len(vehicles)} 件を書き出しました → {OUT_PATH}")


if __name__ == "__main__":
    main()
