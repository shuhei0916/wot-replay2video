"""
Web 上で公開されている好成績リプレイを収集して web.dir に保存する。

取得元（どちらも運営者から「サーバーに負担をかけない方法なら可」と許可済み、2026-11-13）:
  - wot-record.com   : 一覧 HTML（WN8 表示あり）→ /download/<id>
  - skill4ltu.eu     : JSON API /api/replays → /api/replays/<id>/download

負荷をかけないための約束事:
  - リクエストは直列、毎回 web.request_interval_sec 秒あける
  - 一覧は新着順に web.max_pages ページまで、ダウンロードは 1 回 web.max_downloads_per_run 本まで
  - 一度落としたものは採否に関わらず台帳（web.dir/collected.json）に記録し、二度と取りに行かない
  - HTTP エラーが出た取得元はその回は打ち切る（リトライしない）

選別: 一覧の成績から WN8 を見積もって足切り → ダウンロード後にリプレイ本体で
バージョン一致・ランダム戦・WN8 を再検証し、不合格はファイルを捨てて台帳にだけ残す。

使い方:
    python -m src.collect_web            # 収集
    python -m src.collect_web --dry-run  # 一覧の候補を表示するだけ（ダウンロードしない）
"""

import datetime
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from src.config import PROJECT_ROOT, load_config, replays_dir
from src.parse_replay import BattleInfo, parse_replay, read_replay_version
from src.wn8 import battle_wn8

USER_AGENT = "wot-replay-collector/1.0 (low-rate personal tool)"
REPLAY_MAGIC = bytes.fromhex("12323411")
MAX_REPLAY_BYTES = 30 * 1024 * 1024
LEDGER_NAME = "collected.json"

DEFAULTS = {
    "enabled": False,
    "sources": ["wot_record", "skill4ltu"],
    "min_wn8": 4000,
    "tiers": [6, 7, 8, 9, 10, 11],
    "max_downloads_per_run": 5,
    "max_pages": 3,
    "request_interval_sec": 5,
    "own_per_web": 2,
}
# 一覧からの WN8 見積もりは視認・防衛ポイントを 0 と置く（過小評価）ので、足切りは甘めにする
ESTIMATE_MARGIN = 0.85


def web_config() -> dict:
    return {**DEFAULTS, **(load_config().get("web") or {})}


def web_dir() -> Path:
    d = web_config().get("dir")
    return Path(d) if d else PROJECT_ROOT / "replays" / "web"


# ---- 台帳 ----

def load_ledger(directory: Path | None = None) -> dict:
    path = (directory or web_dir()) / LEDGER_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_ledger(ledger: dict, directory: Path | None = None) -> None:
    directory = directory or web_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / LEDGER_NAME).write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def origin_for(replay_name: str, directory: Path | None = None) -> dict | None:
    """リプレイのファイル名から台帳の出典情報を返す。Web 由来でなければ None。"""
    for entry in load_ledger(directory).values():
        if entry.get("file") == replay_name:
            return entry
    return None


def is_web_replay(path: Path) -> bool:
    return Path(path).parent.resolve() == web_dir().resolve()


def build_description(info: BattleInfo, origin: dict) -> str:
    """Web 由来リプレイの概要欄（プレイヤー名と出典）。"""
    return (
        f"Player: {info.player_name} ({info.region})\n"
        f"Replay: {origin.get('page_url', '')}"
    )


def interleave(own: list, web: list, own_per_web: int = 2) -> list:
    """own を own_per_web 本並べるごとに web を 1 本挟む。片方が尽きたら残りを続ける。"""
    if own_per_web <= 0:
        return list(web) + list(own)
    out, w = [], iter(web)
    for i, item in enumerate(own, 1):
        out.append(item)
        if i % own_per_web == 0:
            nxt = next(w, None)
            if nxt is not None:
                out.append(nxt)
    out.extend(w)
    return out


# ---- 候補 ----

@dataclass
class Candidate:
    source: str
    id: str
    page_url: str
    download_url: str
    player: str
    damage: int
    kills: int
    wn8: float | None  # 一覧からの値（wot-record は表示値、skill4ltu は見積もり）

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"


def _num(text: str) -> int:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


_WR_ENTRY_RE = re.compile(r'<a class="tovabb" href="(replay/(\d+)/[^"]*)">(.*?)</a>', re.S)


def _wr_cell(block: str, marker: str) -> int:
    m = re.search(re.escape(marker) + r'[^>]*>\s*<span[^>]*>([^<]*)</span>', block)
    return _num(m.group(1)) if m else 0


def parse_wot_record_listing(page: str) -> list[Candidate]:
    """wot-record.com の一覧 HTML から候補を取り出す。"""
    out = []
    for href, rid, block in _WR_ENTRY_RE.findall(page):
        m = re.search(r'class="date-cont">[^<]*</span>\s*\|\s*([^<]*)<', block)
        out.append(Candidate(
            source="wot_record",
            id=rid,
            page_url=f"https://wot-record.com/{html.unescape(href)}",
            download_url=f"https://wot-record.com/download/{rid}",
            player=html.unescape(m.group(1)).strip() if m else "",
            damage=_wr_cell(block, 'class="icon_damage"'),
            kills=_wr_cell(block, 'class="icon_kills"'),
            wn8=float(_wr_cell(block, 'alt="WN8"')),
        ))
    return out


def parse_skill4ltu_listing(payload: dict) -> list[Candidate]:
    """skill4ltu.eu の /api/replays 応答から候補を取り出す。"""
    out = []
    for it in payload.get("items", []):
        rid = str(it.get("id", ""))
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", rid):
            continue
        damage, kills = int(it.get("damage_dealt") or 0), int(it.get("kills") or 0)
        try:
            est = battle_wn8(int(it.get("vehicle_tank_id") or 0), damage, kills,
                             won=it.get("outcome") == "victory")
        except (TypeError, ValueError):
            est = None
        out.append(Candidate(
            source="skill4ltu",
            id=rid,
            page_url=f"https://skill4ltu.eu/replays?id={rid}",
            download_url=f"https://skill4ltu.eu/api/replays/{rid}/download",
            player=str(it.get("player_name") or ""),
            damage=damage,
            kills=kills,
            wn8=est,
        ))
    return out


def _version_family(version: str, parts: int) -> str:
    return ".".join(version.split(".")[:parts])


def wot_record_url(page: int, version: str, tiers: list[int]) -> str:
    q = f"page={page}&version={_version_family(version, 2)}&game=ctf,domination,assault"
    if tiers:
        q += "&tier=" + ",".join(str(t) for t in tiers)
    return f"https://wot-record.com/replays/date/desc?{q}"


def skill4ltu_url(page: int, version: str, tiers: list[int]) -> str:
    q = {"limit": "10", "p": str(page), "s": "upload",
         "version": _version_family(version, 3), "mode": "random"}
    if tiers:
        q["tier"] = ",".join(str(t) for t in tiers)
    return "https://skill4ltu.eu/api/replays?" + urllib.parse.urlencode(q, safe=",")


SOURCES: dict[str, tuple[Callable[[int, str, list[int]], str], Callable[[str], list[Candidate]]]] = {
    "wot_record": (wot_record_url, parse_wot_record_listing),
    "skill4ltu": (skill4ltu_url, lambda text: parse_skill4ltu_listing(json.loads(text))),
}


def prefilter(cands: Iterable[Candidate], ledger: dict, min_wn8: float) -> list[Candidate]:
    """未取得かつ見積もり WN8 が足切り以上の候補を WN8 降順で返す。"""
    fresh = [
        c for c in cands
        if c.key not in ledger and c.wn8 is not None and c.wn8 >= min_wn8 * ESTIMATE_MARGIN
    ]
    # 同じ戦闘が複数サイトに上がっていることが多いので、成績が一致するものは 1 本にまとめる
    seen, kept = set(), []
    for c in sorted(fresh, key=lambda c: -(c.wn8 or 0)):
        battle = (c.player.lower(), c.damage, c.kills)
        if c.key in seen or battle in seen:
            continue
        seen.update((c.key, battle))
        kept.append(c)
    return kept


# ---- 検証 ----

def verify(info: BattleInfo, target_version: str, min_wn8: float) -> str | None:
    """採用できるなら None、不採用なら理由を返す。"""
    if target_version and info.game_version != target_version:
        return f"version {info.game_version} != {target_version}"
    if not info.is_random_battle:
        return f"battleType {info.battle_type}"
    wn8 = info.wn8
    if wn8 is None:
        return "no WN8 expected values for this vehicle"
    if wn8 < min_wn8:
        return f"wn8 {wn8:.0f} < {min_wn8}"
    return None


def replay_filename(info: BattleInfo, cand: Candidate) -> str:
    """保存名。先頭を YYYYMMDD_HHMM にして自分のリプレイと同じ並びにする。"""
    vehicle = re.sub(r"[^A-Za-z0-9_-]", "", info.player_vehicle)[:60] or "unknown"
    short_id = re.sub(r"[^A-Za-z0-9]", "", cand.id)[:8]
    return f"{info.battle_time:%Y%m%d_%H%M}_{vehicle}_web-{cand.source}-{short_id}.wotreplay"


def target_version() -> str:
    """現行クライアントで再生できるリプレイのバージョン（自分の最新リプレイと同じ）。"""
    own = sorted(p for p in replays_dir().glob("*.wotreplay") if p.name[:8].isdigit())
    return read_replay_version(own[-1]) if own else ""


# ---- 通信（テスト対象外） ----

class _Fetcher:
    def __init__(self, interval: float):
        self.interval = interval
        self._last = 0.0

    def get(self, url: str, max_bytes: int) -> bytes:
        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read(max_bytes + 1)
        finally:
            self._last = time.monotonic()
        if len(data) > max_bytes:
            raise ValueError(f"response too large: {url}")
        return data


def collect(dry_run: bool = False) -> int:
    """収集を 1 回実行し、採用した本数を返す。"""
    cfg = web_config()
    directory = web_dir()
    min_wn8 = float(cfg["min_wn8"])
    version = target_version()
    if not version:
        print("自分のリプレイからクライアントバージョンを特定できないため収集を中止します")
        return 0

    ledger = load_ledger(directory)
    fetcher = _Fetcher(max(1.0, float(cfg["request_interval_sec"])))
    print(f"Web リプレイ収集: version={version} min_wn8={min_wn8:.0f} → {directory}")

    cands: list[Candidate] = []
    for name in cfg["sources"]:
        if name not in SOURCES:
            print(f"  不明な取得元をスキップ: {name}")
            continue
        make_url, parse = SOURCES[name]
        for page in range(1, int(cfg["max_pages"]) + 1):
            try:
                text = fetcher.get(make_url(page, version, cfg["tiers"]), 5 * 1024 * 1024)
                found = parse(text.decode("utf-8", errors="replace"))
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"  {name}: 一覧取得に失敗したため打ち切り: {e}")
                break
            cands += found
            if not found:
                break

    picked = prefilter(cands, ledger, min_wn8)
    print(f"  一覧 {len(cands)} 件 → 未取得かつ足切り通過 {len(picked)} 件")
    if dry_run:
        for c in picked:
            print(f"    WN8~{c.wn8:.0f} {c.damage}dmg {c.kills}kill {c.player} {c.page_url}")
        return 0

    limit = int(cfg["max_downloads_per_run"])
    failed_sources: set[str] = set()
    kept = downloads = 0
    directory.mkdir(parents=True, exist_ok=True)
    for c in picked:
        if kept >= limit or downloads >= limit * 2:
            break
        if c.source in failed_sources:
            continue
        tmp = directory / "download.part"
        try:
            data = fetcher.get(c.download_url, MAX_REPLAY_BYTES)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  {c.source}: ダウンロード失敗のためこの取得元を打ち切り: {e}")
            failed_sources.add(c.source)
            continue
        downloads += 1

        entry = {
            "source": c.source, "id": c.id, "page_url": c.page_url,
            "collected_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "file": None,
        }
        reason = None
        info = None
        if data[:4] != REPLAY_MAGIC:
            reason = "not a replay file"
        else:
            tmp.write_bytes(data)
            try:
                info = parse_replay(tmp)
                reason = verify(info, version, min_wn8)
            except Exception as e:  # 壊れたファイル・戦闘結果なし等
                reason = f"parse error: {type(e).__name__}"

        if reason is None and info is not None:
            name = replay_filename(info, c)
            entry.update(player=info.player_name, region=info.region, wn8=round(info.wn8 or 0))
            if (directory / name).exists() or any(
                e.get("player") == info.player_name and e.get("battle_time") == info.battle_time.isoformat()
                for e in ledger.values()
            ):
                reason = "duplicate battle"
            else:
                tmp.replace(directory / name)
                entry.update(file=name, status="kept", battle_time=info.battle_time.isoformat())
                kept += 1
                print(f"  採用 WN8 {info.wn8:.0f}: {name}")
        if reason is not None:
            tmp.unlink(missing_ok=True)
            entry.update(status="rejected", reason=reason)
            print(f"  不採用（{reason}）: {c.page_url}")

        ledger[c.key] = entry
        save_ledger(ledger, directory)

    print(f"  完了: {downloads} 本ダウンロード、{kept} 本採用")
    return kept


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    collect(dry_run="--dry-run" in sys.argv)
