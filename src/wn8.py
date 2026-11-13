"""
1戦単位の WN8 を計算する。

期待値は XVM 配布の wn8exp.json（src/data/wn8exp.json に同梱。更新は
https://static.modxvm.com/wn8-data-exp/json/wn8exp.json を上書き）。
車両は typeCompDescr（= 期待値表の IDNum）で引く。期待値表に無い車両
（新規実装車両等）は None を返すので、呼び出し側でフォールバックすること。

1戦 WN8 は勝敗を勝率 100% / 0% として扱うため、通算 WN8 より振れ幅が大きい。
"""

import json
from functools import lru_cache
from pathlib import Path

EXPECTED_PATH = Path(__file__).parent / "data" / "wn8exp.json"


@lru_cache(maxsize=1)
def _expected() -> dict[int, dict]:
    try:
        data = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))["data"]
    except (OSError, ValueError, KeyError):
        return {}
    return {int(e["IDNum"]): e for e in data}


def battle_wn8(
    tank_id: int,
    damage: float,
    frags: float,
    spotted: float = 0,
    defence: float = 0,
    won: bool = False,
) -> float | None:
    """1戦の成績から WN8 を返す。期待値の無い車両は None。"""
    exp = _expected().get(int(tank_id))
    if not exp:
        return None

    def ratio(value: float, key: str) -> float:
        e = exp.get(key) or 0
        return value / e if e > 0 else 0.0

    r_dmg = ratio(damage, "expDamage")
    r_frag = ratio(frags, "expFrag")
    r_spot = ratio(spotted, "expSpot")
    r_def = ratio(defence, "expDef")
    r_win = ratio(100.0 if won else 0.0, "expWinRate")

    c_win = max(0.0, (r_win - 0.71) / (1 - 0.71))
    c_dmg = max(0.0, (r_dmg - 0.22) / (1 - 0.22))
    c_frag = max(0.0, min(c_dmg + 0.2, (r_frag - 0.12) / (1 - 0.12)))
    c_spot = max(0.0, min(c_dmg + 0.1, (r_spot - 0.38) / (1 - 0.38)))
    c_def = max(0.0, min(c_dmg + 0.1, (r_def - 0.10) / (1 - 0.10)))

    return (
        980 * c_dmg
        + 210 * c_dmg * c_frag
        + 155 * c_frag * c_spot
        + 75 * c_def * c_frag
        + 145 * min(1.8, c_win)
    )
