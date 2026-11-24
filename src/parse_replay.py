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

RANDOM_BATTLE_TYPE = 1  # Block 1 の battleType。ランダム戦以外（Story Mode/Epic Random等）は別の値になる


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
    achievements: list[int] = field(default_factory=list)
    type_comp_descr: int = 0          # 車両ID（WN8 期待値表の IDNum）
    dropped_capture_points: int = 0   # 防衛ポイント（WN8 の DEF 項）

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
    battle_type: int = RANDOM_BATTLE_TYPE
    all_vehicles: list[dict] = field(default_factory=list)

    @property
    def player_won(self) -> bool:
        return self.player_team == self.winner_team

    @property
    def is_random_battle(self) -> bool:
        return self.battle_type == RANDOM_BATTLE_TYPE

    @property
    def wn8(self) -> float | None:
        """この1戦の WN8。期待値表に無い車両は None。"""
        from src.wn8 import battle_wn8

        s = self.player_stats
        return battle_wn8(
            s.type_comp_descr, s.damage_dealt, s.kills, s.spotted,
            s.dropped_capture_points, self.player_won,
        )


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


def read_replay_version(path: Path) -> str:
    """
    リプレイのクライアントバージョン（clientVersionFromExe）を返す。
    Block 1 だけを読むため高速（Google Drive 上のファイルでも全読みしない）。
    読めない場合は空文字。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            size = struct.unpack_from("<I", head, 8)[0]
            b1 = json.loads(f.read(size).decode("utf-8"))
        return b1.get("clientVersionFromExe", "")
    except Exception:
        return ""


def _vehicle_display_name(vehicle_tag: str) -> str:
    """
    'japan-J28_O_I_100' → 'O-Ni' のように公式表示名を返す。

    まず src/data/vehicle_names.json（クライアントの .mo から抽出した公式名）を
    引く。WG の車両改名・再編で内部タグと現行の公式名がズレることがあるため
    （例: 'china-Ch43_WZ_122_2' の現行名は「122 TM」）、単純な文字列加工では
    正しい名前にならない。未収録のタグ（新規車両等）は文字列加工にフォールバックする。
    """
    from src.vehicle_names import official_vehicle_name

    official = official_vehicle_name(vehicle_tag)
    if official:
        return official

    after_nation = vehicle_tag.split("-", 1)[-1]  # 'J28_O_I_100'
    after_code = after_nation.split("_", 1)[-1]   # 'O_I_100'
    return after_code.replace("_", " ")


TITLE_LANGS = ("en", "ja")
TITLE_SEPARATOR = " | "
MAX_TITLE_LEN = 100  # YouTube のタイトル上限（超えると invalidTitle で 400。2026-11-19）


def fit_title(title: str, max_len: int = MAX_TITLE_LEN) -> str:
    """
    タイトルを max_len 文字以内に収める。

    " | " 区切りの末尾項目（優先度の低い勲章）から順に落とす。
    先頭項目（車両名）だけでも超える場合は文字数で切り詰める。
    """
    parts = title.split(TITLE_SEPARATOR)
    while len(parts) > 1 and len(TITLE_SEPARATOR.join(parts)) > max_len:
        parts.pop()
    return TITLE_SEPARATOR.join(parts)[:max_len]


MIN_KILLS_IN_TITLE = 4  # これ未満のキル数はアピールにならないのでタイトルに出さない（2026-11-24）


def format_damage(damage: int) -> str:
    """
    ダメージの簡易表記: 1,000 未満はそのまま、以上は 0.1k 単位で切り捨て。
    例: 974 → '974', 2047 → '2k', 4560 → '4.5k'（切り上げると実際より盛って見えるため切り捨て）
    """
    if damage < 1000:
        return str(damage)
    return f"{damage // 100 / 10:g}k"


def generate_title(info: "BattleInfo", lang: str = "ja") -> str:
    """
    BattleInfo からショート動画用タイトルを生成する（lang: "ja" / "en"）。

    形式: 「タンク名 | ダメージ（簡易表記） | キル数（4キル以上のみ） | 勲章（あれば）」
    例: 「Leopard 1 | 4.5k Damage | 4kill」（2026-11-24 方針。旧: 車両, キル, ダメ）。
    区切りは " | "（カンマだと数字の桁区切りと紛れるため。2026-11-13）。
    LLM は使わないシンプルな固定テンプレート（LLM 版は 2026-11-24 に比較して見送り）。
    100 文字を超える場合は末尾の勲章から省く（fit_title）。
    車両名はクライアント収録の公式名（英字）なので言語共通。
    """
    from src.achievements import notable_medals

    vehicle = _vehicle_display_name(info.player_vehicle)
    s = info.player_stats
    damage = format_damage(s.damage_dealt)
    show_kills = s.kills >= MIN_KILLS_IN_TITLE
    if lang == "en":
        parts = [vehicle, f"{damage} Damage"] + ([f"{s.kills}kill"] if show_kills else [])
    else:
        parts = [vehicle, f"{damage}ダメージ"] + ([f"{s.kills}キル"] if show_kills else [])
    parts += notable_medals(s.achievements, lang=lang)
    return fit_title(TITLE_SEPARATOR.join(parts))


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
        achievements=personal_entry.get("achievements", []),
        type_comp_descr=personal_entry.get("typeCompDescr", 0),
        dropped_capture_points=personal_entry.get("droppedCapturePoints", 0),
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
        battle_type=b1.get("battleType", RANDOM_BATTLE_TYPE),
        all_vehicles=all_vehicles,
    )
