"""
他チャンネルの人気 WoT 系 Shorts のタイトルを YouTube Data API で収集・蓄積する。

将来 LLM タイトル生成に戻すときの few-shot 手本用（todo.md 2026-08-04 着想）。
今は収集だけで、タイトル生成には使っていない。

- search.list で「直近 N 日に公開された、再生数順の短尺動画」を検索語ごとに取得
- videos.list で尺・再生数を取り、Shorts の尺（max_duration_sec 以下）だけ残す
- 台帳 output/popular_titles.json に video_id 単位で追記。既知の動画は
  再生数・最終確認日時を更新する（伸び方を後で見られるよう履歴も残す）

クォータ: search.list = 100 unit/回、videos.list = 1 unit/回。アップロード
（videos.insert = 1600 unit × 最大5本）と同じ日次 10000 unit を分け合うため、
検索語は少数に絞り、interval_hours 以内の再実行はスキップする。

使い方:
    python -m src.collect_titles            # 収集（間隔未満ならスキップ）
    python -m src.collect_titles --force    # 間隔を無視して収集
    python -m src.collect_titles --top 30   # 蓄積済みの上位タイトルを表示（API 不使用）
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

from src.config import OUTPUT_DIR, load_config

LEDGER_PATH = OUTPUT_DIR / "popular_titles.json"

DEFAULTS = {
    "enabled": True,
    "queries": ["World of Tanks #shorts", "WoT shorts", "ワールドオブタンクス #shorts"],
    "published_within_days": 30,   # この日数以内に公開された動画だけ検索
    "results_per_query": 50,       # search.list の maxResults（上限 50）
    "max_duration_sec": 180,       # Shorts の尺上限（2024-10 から最大 3 分）
    "min_views": 10000,            # これ未満は「人気」とみなさず台帳に入れない
    "interval_hours": 20,          # 前回収集からこの時間未満なら実行しない
}

_DURATION_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


# ---------------------------------------------------------------------------
# 純粋ロジック（テスト対象）
# ---------------------------------------------------------------------------

def titles_config() -> dict:
    """config.yaml の title_research を既定値で補完して返す。"""
    return {**DEFAULTS, **(load_config().get("title_research") or {})}


def parse_iso_duration(s: str) -> int | None:
    """'PT1M5S' → 65。解釈できなければ None。"""
    m = _DURATION_RE.match(s or "")
    if not m or s in ("P", "PT"):
        return None
    d, h, mi, sec = (int(g) if g else 0 for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + sec


def video_record(item: dict, query: str, now: datetime.datetime) -> dict | None:
    """videos.list の1件を台帳レコードにする。尺が取れなければ None。"""
    duration = parse_iso_duration(item.get("contentDetails", {}).get("duration", ""))
    if duration is None:
        return None
    sn = item.get("snippet", {})
    st = item.get("statistics", {})
    return {
        "video_id": item["id"],
        "title": sn.get("title", ""),
        "channel_id": sn.get("channelId", ""),
        "channel_title": sn.get("channelTitle", ""),
        "published_at": sn.get("publishedAt", ""),
        "language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "",
        "duration_sec": duration,
        "views": int(st.get("viewCount", 0)),
        "likes": int(st.get("likeCount", 0)),
        "comments": int(st.get("commentCount", 0)),
        "queries": [query],
        "first_seen": now.isoformat(timespec="seconds"),
        "last_seen": now.isoformat(timespec="seconds"),
        "view_history": [[now.date().isoformat(), int(st.get("viewCount", 0))]],
    }


def is_popular_short(rec: dict, max_duration_sec: int, min_views: int, own_channel_id: str = "") -> bool:
    """Shorts の尺に収まり、再生数が基準以上で、自チャンネルでないか。"""
    return (
        0 < rec["duration_sec"] <= max_duration_sec
        and rec["views"] >= min_views
        and (not own_channel_id or rec["channel_id"] != own_channel_id)
    )


def merge_records(ledger: dict, new: list[dict]) -> tuple[int, int]:
    """
    台帳 {video_id: record} に新しいレコードを取り込む。(追加数, 更新数) を返す。
    既知の動画はタイトル（後から変わることがある）・統計・last_seen を更新し、
    検索語と再生数の日次履歴（同じ日は上書き）を足す。first_seen は保持。
    """
    added = updated = 0
    for rec in new:
        old = ledger.get(rec["video_id"])
        if old is None:
            ledger[rec["video_id"]] = rec
            added += 1
            continue
        for k in ("title", "channel_title", "views", "likes", "comments", "last_seen", "duration_sec"):
            old[k] = rec[k]
        for q in rec["queries"]:
            if q not in old["queries"]:
                old["queries"].append(q)
        hist = old.setdefault("view_history", [])
        day, views = rec["view_history"][-1]
        if hist and hist[-1][0] == day:
            hist[-1] = [day, views]
        else:
            hist.append([day, views])
        updated += 1
    return added, updated


def views_per_day(rec: dict, now: datetime.datetime) -> float:
    """公開から最終確認までの1日あたり再生数（公開直後の過大評価を避けるため最低1日）。"""
    try:
        pub = datetime.datetime.fromisoformat(rec["published_at"].replace("Z", "+00:00"))
        seen = datetime.datetime.fromisoformat(rec.get("last_seen") or now.isoformat())
    except ValueError:
        return 0.0
    days = max((seen - pub).total_seconds() / 86400, 1.0)
    return rec["views"] / days


def top_titles(ledger: dict, n: int, now: datetime.datetime, per_channel: int = 3) -> list[dict]:
    """
    1日あたり再生数の多い順に n 件。1チャンネルが独占しないよう per_channel 件まで。
    （few-shot の手本選びに使う想定）
    """
    ranked = sorted(ledger.values(), key=lambda r: views_per_day(r, now), reverse=True)
    out, count = [], {}
    for r in ranked:
        c = r.get("channel_id", "")
        if count.get(c, 0) >= per_channel:
            continue
        count[c] = count.get(c, 0) + 1
        out.append(r)
        if len(out) >= n:
            break
    return out


def is_due(meta: dict, interval_hours: float, now: datetime.datetime) -> bool:
    last = meta.get("last_run")
    if not last:
        return True
    try:
        return now - datetime.datetime.fromisoformat(last) >= datetime.timedelta(hours=interval_hours)
    except ValueError:
        return True


def load_ledger(path: Path = LEDGER_PATH) -> dict:
    """{"meta": {...}, "videos": {video_id: record}}。無い・壊れていれば空。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("meta", {})
        data.setdefault("videos", {})
        return data
    except (OSError, ValueError):
        return {"meta": {}, "videos": {}}


def save_ledger(data: dict, path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# API アクセス（テスト対象外）
# ---------------------------------------------------------------------------

def _youtube():
    import googleapiclient.discovery
    from src.weekly_report import get_credentials  # 閲覧専用トークン（search に十分）
    return googleapiclient.discovery.build(
        "youtube", "v3", credentials=get_credentials(), cache_discovery=False
    )


def collect(force: bool = False, now: datetime.datetime | None = None) -> None:
    cfg = titles_config()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not cfg["enabled"]:
        print("タイトル収集: 無効（title_research.enabled=false）")
        return
    data = load_ledger()
    if not force and not is_due(data["meta"], cfg["interval_hours"], now):
        print(f"タイトル収集: 前回 {data['meta']['last_run']} から {cfg['interval_hours']}h 未満のためスキップ")
        return

    yt = _youtube()
    own = yt.channels().list(part="id", mine=True).execute().get("items", [])
    own_id = own[0]["id"] if own else ""
    after = (now - datetime.timedelta(days=cfg["published_within_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")

    new = []
    for q in cfg["queries"]:
        r = yt.search().list(
            part="id", q=q, type="video", videoDuration="short", order="viewCount",
            publishedAfter=after, maxResults=min(int(cfg["results_per_query"]), 50),
        ).execute()
        ids = [i["id"]["videoId"] for i in r.get("items", []) if i.get("id", {}).get("videoId")]
        if not ids:
            continue
        vr = yt.videos().list(part="snippet,contentDetails,statistics", id=",".join(ids)).execute()
        kept = 0
        for item in vr.get("items", []):
            rec = video_record(item, q, now)
            if rec and is_popular_short(rec, cfg["max_duration_sec"], cfg["min_views"], own_id):
                new.append(rec)
                kept += 1
        print(f"  「{q}」: {len(ids)} 件中 {kept} 件を採用")

    added, updated = merge_records(data["videos"], new)
    data["meta"]["last_run"] = now.isoformat(timespec="seconds")
    save_ledger(data)
    print(f"タイトル収集: 新規 {added} / 更新 {updated}（台帳 {len(data['videos'])} 件）")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="人気 WoT Shorts のタイトル収集")
    ap.add_argument("--force", action="store_true", help="収集間隔を無視して実行")
    ap.add_argument("--top", type=int, metavar="N", help="蓄積済みの上位 N 件を表示して終了")
    args = ap.parse_args()

    if args.top:
        now = datetime.datetime.now(datetime.timezone.utc)
        for r in top_titles(load_ledger()["videos"], args.top, now):
            print(f"{views_per_day(r, now):>10,.0f}/日 {r['views']:>11,}回  {r['title']}  [{r['channel_title']}]")
        return 0
    collect(force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
