"""
バトルリザルトの勲章 ID → タイトルに載せる価値のある勲章名（日本語）の解決。

ID → 名前の対応表は tools/extract_achievements.py が WoT クライアントから
生成した src/data/achievements.json（コミット済み静的データ）を使う。
DB ID は dossiers2 の RECORD_DB_IDS 由来の安定 ID なので、クライアント更新で
変わることは基本ないが、新勲章の追加時はツールを再実行して更新する。
"""

import json
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "achievements.json"

# エピック勲章（dossiers2 EPIC_MEDAL_SET 相当）。1戦で取るのが極めて難しい、
# タイトルに必ず入れたいもの
EPIC_MEDALS = {
    "medalKolobanov",
    "medalLafayettePool",
    "medalRadleyWalters",
    "heroesOfRassenay",
    "medalBillotte",
    "medalBurda",
    "medalDumitru",
    "medalOskin",
    "medalNikolas",
    "medalOrlik",
    "medalFadin",
    "medalDeLanglade",
    "medalGore",
    "huntsman",
    "medalTamadaYoshio",
    "medalHalonen",
    "medalLehvaslaiho",
    "medalPascucci",
    "medalTarczay",
    "medalBrunoPietro",
    "medalStark",
}

# バトルヒーロー勲章（dossiers2 BATTLE_HERO_MEDAL_SET 相当）。エピックほどでは
# ないが見せ場の根拠になるもの
BATTLE_HERO_MEDALS = {
    "warrior",       # トップガン
    "invader",
    "sniper",
    "sniper2",
    "mainGun",
    "defender",
    "steelwall",
    "supporter",
    "scout",
    "evileye",
}


@lru_cache(maxsize=1)
def _load() -> dict[int, dict]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {int(k): v for k, v in data["achievements"].items()}


# タイトル対象勲章の英語名（WoT 英語クライアントでの表記）。
# ASIA クライアントには日本語の .mo しか無く抽出できないため静的辞書で持つ。
EN_NAMES = {
    "medalKolobanov": "Kolobanov's Medal",
    "medalLafayettePool": "Pool's Medal",
    "medalRadleyWalters": "Radley-Walters' Medal",
    "heroesOfRassenay": "Raseiniai Heroes Medal",
    "medalBillotte": "Billotte's Medal",
    "medalBurda": "Burda's Medal",
    "medalDumitru": "Dumitru's Medal",
    "medalOskin": "Oskin's Medal",
    "medalNikolas": "Nicols' Medal",
    "medalOrlik": "Orlik's Medal",
    "medalFadin": "Fadin's Medal",
    "medalDeLanglade": "de Langlade's Medal",
    "medalGore": "Gore's Medal",
    "huntsman": "Naidin's Medal",
    "medalTamadaYoshio": "Tamada Yoshio's Medal",
    "medalHalonen": "Halonen's Medal",
    "medalLehvaslaiho": "Lehväslaiho's Medal",
    "medalPascucci": "Pascucci's Medal",
    "medalTarczay": "Tarczay's Medal",
    "medalBrunoPietro": "Bruno's Medal",
    "medalStark": "Stark's Medal",
    "warrior": "Top Gun",
    "invader": "Invader",
    "sniper": "Sniper",
    "sniper2": "Tank Sniper",
    "mainGun": "High Caliber",
    "defender": "Defender",
    "steelwall": "Steel Wall",
    "supporter": "Confederate",
    "scout": "Scout",
    "evileye": "Patrol Duty",
}


def notable_medals(achievement_ids: list[int], lang: str = "ja") -> list[str]:
    """
    勲章 ID のリストから、タイトルに載せる価値のある勲章名を返す（lang: "ja" / "en"）。

    エピック勲章 → バトルヒーロー勲章の順。該当なしなら空リスト。
    """
    table = _load()
    epic, hero = [], []
    for aid in achievement_ids:
        entry = table.get(aid)
        if entry is None:
            continue
        name = entry["name"]
        label = EN_NAMES.get(name) if lang == "en" else entry.get("ja")
        if not label:
            continue
        if name in EPIC_MEDALS:
            epic.append(label)
        elif name in BATTLE_HERO_MEDALS:
            hero.append(label)
    return epic + hero
