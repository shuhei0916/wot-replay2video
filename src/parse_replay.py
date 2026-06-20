"""
.wotreplay ファイルを解析してバトル情報を返す。

ファイル構造:
  [4B] magic
  [4B] ブロック数 (通常 2)
  [4B + NB] Block 1 (JSON): バトル開始前メタデータ
  [4B + NB] Block 2 (JSON): バトル結果サマリー [common, personal, vehicles, players, ...]
  [残り]     バイナリ: ゲームパケット録画 (AES 暗号化)
"""

import datetime
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PlayerStats:
    kills: int
    damage_dealt: int
    shots: int
    direct_hits: int
    survived: bool
    hp_remaining: int
    spotted: int
    damage_assisted_radio: int
    xp: int
    credits: int
    mark_of_mastery: int

    @property
    def hit_rate(self) -> float:
        return self.direct_hits / self.shots if self.shots > 0 else 0.0


@dataclass
class BattleInfo:
    player_name: str
    player_vehicle: str
    map_name: str
    game_version: str
    region: str
    battle_time: datetime.datetime
    duration_seconds: int
    winner_team: int
    player_team: int
    player_stats: PlayerStats
    all_vehicles: list[dict] = field(default_factory=list)

    @property
    def player_won(self) -> bool:
        return self.player_team == self.winner_team


def _read_blocks(data: bytes) -> tuple[dict, list]:
    """バイナリから JSON ブロックを読み出す。"""
    num_blocks = struct.unpack_from("<I", data, 4)[0]
    offset = 8
    blocks = []
    for _ in range(num_blocks):
        size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        blocks.append(json.loads(data[offset : offset + size].decode("utf-8")))
        offset += size
    return blocks


def _vehicle_display_name(vehicle_tag: str) -> str:
    """'japan-J28_O_I_100' → 'O I 100' のように表示名を抽出する。"""
    after_nation = vehicle_tag.split("-", 1)[-1]  # 'J28_O_I_100'
    after_code = after_nation.split("_", 1)[-1]   # 'O_I_100'
    return after_code.replace("_", " ")


def generate_title(info: "BattleInfo") -> str:
    """BattleInfo からショート動画用タイトルを生成する。"""
    vehicle = _vehicle_display_name(info.player_vehicle)
    s = info.player_stats
    result = "勝利" if info.player_won else "敗北"
    survived = "" if s.survived else "（撃破）"
    return (
        f"【WoT】{vehicle} / {s.kills}kill / {s.damage_dealt:,}DMG"
        f" / {info.map_name} / {result}{survived} #Shorts #WorldOfTanks"
    )


def parse_replay(path: Path) -> BattleInfo:
    """
    .wotreplay ファイルを解析して BattleInfo を返す。

    Raises:
        FileNotFoundError: ファイルが存在しない場合
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"リプレイファイルが見つかりません: {path}")

    data = path.read_bytes()
    blocks = _read_blocks(data)

    b1 = blocks[0]
    b2_item = blocks[1][0]

    common = b2_item["common"]
    personal_map = b2_item["personal"]

    # personal の実プレイヤーエントリを取得（"avatar" キーを除外）
    personal_entry = next(
        v for k, v in personal_map.items() if k != "avatar"
    )

    player_stats = PlayerStats(
        kills=personal_entry["kills"],
        damage_dealt=personal_entry["damageDealt"],
        shots=personal_entry["shots"],
        direct_hits=personal_entry["directEnemyHits"],
        survived=personal_entry["deathReason"] == -1,
        hp_remaining=personal_entry["health"],
        spotted=personal_entry["spotted"],
        damage_assisted_radio=personal_entry["damageAssistedRadio"],
        xp=personal_entry["xp"],
        credits=personal_entry["credits"],
        mark_of_mastery=personal_entry["markOfMastery"],
    )

    # Block 1 の vehicles から vehicleID → 名前のマップを作る
    b1_vehicles = b1.get("vehicles", {})
    name_by_vehicle_id = {vid: v.get("name", "") for vid, v in b1_vehicles.items()}

    all_vehicles = []
    for vehicle_id, vlist in b2_item.get("vehicles", {}).items():
        for v in vlist:
            name = name_by_vehicle_id.get(vehicle_id, "")
            all_vehicles.append({
                "vehicle_id": vehicle_id,
                "name": name,
                "team": v.get("team"),
                "kills": v.get("kills", 0),
                "damage_dealt": v.get("damageDealt", 0),
                "survived": v.get("deathReason", 0) == -1,
            })

    # Block 1 の vehicles から自プレイヤーのチームを取得
    player_team = personal_entry["team"]

    battle_time = datetime.datetime.fromtimestamp(common["arenaCreateTime"])

    return BattleInfo(
        player_name=b1["playerName"],
        player_vehicle=b1.get("playerVehicle", ""),
        map_name=b1["mapDisplayName"],
        game_version=b1["clientVersionFromExe"],
        region=b1["regionCode"],
        battle_time=battle_time,
        duration_seconds=common["duration"],
        winner_team=common["winnerTeam"],
        player_team=player_team,
        player_stats=player_stats,
        all_vehicles=all_vehicles,
    )
