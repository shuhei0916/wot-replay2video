"""
車両タグ（.wotreplay の playerVehicle）→ 公式表示名の解決。

対応表は tools/extract_vehicle_names.py が WoT クライアントから生成した
src/data/vehicle_names.json（コミット済み静的データ）を使う。WG の車両改名・
再編で内部タグと現行の公式名がズレることがあるため（例:
'china-Ch43_WZ_122_2' は現在「122 TM」）、単純な文字列加工では正しい名前に
ならない。クライアント更新後、新規追加車両を反映するにはツールを再実行する。
"""

import json
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "vehicle_names.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return data["vehicles"]


def official_vehicle_name(vehicle_tag: str) -> str | None:
    """
    'sweden-S29_UDES_14_5' のようなタグから公式表示名を返す。
    対応表に無ければ None（呼び出し側は文字列加工にフォールバックする）。
    """
    return _load().get(vehicle_tag)
