"""
週次レポート: チャンネルの推移と進捗をまとめたメール本文（プレーンテキスト）を作る。

クラウドルーティン（PC が付いていなくても動く）から実行し、Gmail で送る想定。
YouTube へのアクセスは閲覧専用スコープの別トークンを使う（アップロード権限を
クラウドに置かないため）。

認証情報の解決順:
  1. 環境変数 YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN（クラウド用）
  2. config/token_readonly.json（ローカル。無ければブラウザ認証して作る）

使い方:
    python -m src.weekly_report              # 本文を標準出力へ
    python -m src.weekly_report --auth       # ローカルで閲覧専用トークンを作り、
                                             # クラウドに登録する値を表示する
"""

import datetime
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
TOKEN_PATH = CONFIG_DIR / "token_readonly.json"
SECRETS_PATH = CONFIG_DIR / "client_secrets.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

JST = datetime.timezone(datetime.timedelta(hours=9))
WEEKS_OF_HISTORY = 8
PUBLISH_SLOTS_PER_DAY = 3  # config youtube.schedule.times の本数（ストック日数の換算用）


# ---------------------------------------------------------------------------
# 純粋ロジック（テスト対象）
# ---------------------------------------------------------------------------

@dataclass
class DayStats:
    day: datetime.date
    views: int
    minutes_watched: float
    avg_view_pct: float
    subs_gained: int
    subs_lost: int


def subscriber_history(
    current_subs: int, days: list[DayStats], as_of: datetime.date
) -> dict[datetime.date, int]:
    """
    現在の登録者数と日次の増減から、各日の終わり時点の登録者数を逆算する。

    Analytics は数日遅れなので、days の最終日より後（データ未確定）の増減は
    0 とみなす。as_of 以前の日だけを対象にする。
    """
    by_day = {d.day: d.subs_gained - d.subs_lost for d in days}
    out: dict[datetime.date, int] = {}
    subs = current_subs
    day = as_of
    first = min(by_day) if by_day else as_of
    while day >= first:
        out[day] = subs
        subs -= by_day.get(day, 0)
        day -= datetime.timedelta(days=1)
    return out


def weekly_buckets(
    days: list[DayStats], end: datetime.date, weeks: int = WEEKS_OF_HISTORY
) -> list[tuple[datetime.date, datetime.date, list[DayStats]]]:
    """end を最終日とする直近 weeks 週（7日ごと）に日次データを分ける。古い順。"""
    out = []
    for w in range(weeks - 1, -1, -1):
        w_end = end - datetime.timedelta(days=7 * w)
        w_start = w_end - datetime.timedelta(days=6)
        out.append((w_start, w_end, [d for d in days if w_start <= d.day <= w_end]))
    return out


def scheduled_stock(
    videos: list[dict], now: datetime.datetime
) -> tuple[int, datetime.datetime | None]:
    """
    予約投稿のストック（未来の publishAt を持つ動画数）と、最後の予約日時を返す。
    videos は videos.list の item（status.publishAt を含む）。
    """
    future = []
    for v in videos:
        pa = (v.get("status") or {}).get("publishAt")
        if not pa:
            continue
        t = datetime.datetime.fromisoformat(pa.replace("Z", "+00:00"))
        if t > now:
            future.append(t)
    return len(future), (max(future) if future else None)


def _fmt_delta(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def format_report(
    *,
    channel_title: str,
    now: datetime.datetime,
    current_subs: int,
    total_views: int,
    video_count: int,
    days: list[DayStats],
    data_through: datetime.date | None,
    published_this_week: list[dict],
    stock_count: int,
    stock_until: datetime.datetime | None,
    top_videos: list[tuple[str, int, float]],
    commits: list[str],
    open_todos: list[str],
) -> str:
    """メール本文（プレーンテキスト）を組み立てる。"""
    today = now.astimezone(JST).date()
    lines = [
        f"{channel_title} 週次レポート（{today:%Y-%m-%d}）",
        "",
        "■ サマリー",
        f"  登録者: {current_subs:,} 人 / 総再生: {total_views:,} 回 / 公開動画: {video_count:,} 本",
    ]

    end = data_through or today
    buckets = weekly_buckets(days, end)
    history = subscriber_history(current_subs, days, end)
    if buckets and buckets[-1][2]:
        last = buckets[-1][2]
        prev = buckets[-2][2] if len(buckets) > 1 else []
        v_last, v_prev = sum(d.views for d in last), sum(d.views for d in prev)
        net_last = sum(d.subs_gained - d.subs_lost for d in last)
        lines.append(
            f"  直近7日（〜{end:%m/%d}）: 再生 {v_last:,} 回（前週 {v_prev:,}）、"
            f"登録者 {_fmt_delta(net_last)} 人"
        )
    lines.append(f"  ※ YouTube Analytics は2〜3日遅れ。集計は {end:%m/%d} まで")

    lines += ["", f"■ 週ごとの推移（直近{WEEKS_OF_HISTORY}週）",
              "  週          | 再生   | 視聴(分) | 平均視聴率 | 登録者増減 | 週末の登録者"]
    for w_start, w_end, ds in buckets:
        views = sum(d.views for d in ds)
        minutes = sum(d.minutes_watched for d in ds)
        pct = (sum(d.avg_view_pct * d.views for d in ds) / views) if views else 0.0
        net = sum(d.subs_gained - d.subs_lost for d in ds)
        subs_end = history.get(w_end, "-")
        lines.append(
            f"  {w_start:%m/%d}-{w_end:%m/%d} | {views:>6,} | {minutes:>8,.0f} | "
            f"{pct:>9.1f}% | {_fmt_delta(net):>10} | {subs_end:>6}"
        )

    lines += ["", "■ 投稿"]
    lines.append(f"  今週公開された動画: {len(published_this_week)} 本")
    if stock_until is not None:
        days_left = (stock_until.astimezone(JST).date() - today).days
        lines.append(
            f"  予約ストック: {stock_count} 本（{stock_until.astimezone(JST):%m/%d %H:%M} まで、"
            f"あと約 {days_left} 日分）"
        )
        if days_left < 7:
            lines.append("  ⚠ ストックが1週間を切っています。出かける前に録画バッチを回してください")
    else:
        lines.append("  予約ストック: 0 本 ⚠ 録画バッチを回してください")

    if top_videos:
        lines += ["", "■ 直近7日の再生上位"]
        for title, views, pct in top_videos:
            lines.append(f"  {views:>5,} 回 / 平均視聴率 {pct:.0f}% : {title}")

    lines += ["", "■ 開発の進捗（直近7日のコミット）"]
    lines += [f"  - {c}" for c in commits] or ["  （コミットなし）"]

    if open_todos:
        lines += ["", "■ 次にやること（todo.md）"]
        lines += [f"  - {t}" for t in open_todos]

    return "\n".join(lines) + "\n"


def open_todos_from_markdown(text: str, section: str = "## 次にやる", limit: int = 8) -> list[str]:
    """todo.md の指定セクションから未完了 [ ] 項目の見出し（太字部分 or 先頭行）を取り出す。"""
    import re
    out = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == section
            continue
        if in_section and line.startswith("- [ ]"):
            body = line[5:].strip()
            m = re.search(r"\*\*(.+?)\*\*", body)
            out.append(m.group(1) if m else body[:60])
    return out[:limit]


# ---------------------------------------------------------------------------
# API・環境アクセス（テスト対象外）
# ---------------------------------------------------------------------------

def get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    env = {k: os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")}
    if all(env.values()):
        creds = Credentials(
            None,
            refresh_token=env["YT_REFRESH_TOKEN"],
            client_id=env["YT_CLIENT_ID"],
            client_secret=env["YT_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        creds.refresh(Request())
        return creds

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _fetch_all_uploads(yt, uploads_playlist: str) -> list[dict]:
    ids = []
    token = None
    while True:
        r = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads_playlist, maxResults=50, pageToken=token
        ).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    videos = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,status,statistics", id=",".join(ids[i:i + 50])).execute()
        videos += r.get("items", [])
    return videos


def _git_commits(since_days: int = 7) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "log", f"--since={since_days} days ago", "--pretty=%ad %s", "--date=format:%m/%d"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def build_report(now: datetime.datetime | None = None) -> str:
    import googleapiclient.discovery

    now = now or datetime.datetime.now(datetime.timezone.utc)
    creds = get_credentials()
    yt = googleapiclient.discovery.build("youtube", "v3", credentials=creds, cache_discovery=False)
    ya = googleapiclient.discovery.build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)

    ch = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()["items"][0]
    stats = ch["statistics"]

    today = now.astimezone(JST).date()
    start = today - datetime.timedelta(days=7 * WEEKS_OF_HISTORY + 7)
    rep = ya.reports().query(
        ids="channel==MINE", startDate=start.isoformat(), endDate=today.isoformat(),
        metrics="views,estimatedMinutesWatched,averageViewPercentage,subscribersGained,subscribersLost",
        dimensions="day", sort="day",
    ).execute()
    days = [
        DayStats(datetime.date.fromisoformat(r[0]), int(r[1]), float(r[2]), float(r[3]), int(r[4]), int(r[5]))
        for r in rep.get("rows", [])
    ]
    data_through = max((d.day for d in days), default=None)

    videos = _fetch_all_uploads(yt, ch["contentDetails"]["relatedPlaylists"]["uploads"])
    stock_count, stock_until = scheduled_stock(videos, now)
    week_ago = now - datetime.timedelta(days=7)
    published = [
        v for v in videos
        if v["status"].get("privacyStatus") == "public"
        and datetime.datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00")) >= week_ago
    ]

    top_end = data_through or today
    top = ya.reports().query(
        ids="channel==MINE",
        startDate=(top_end - datetime.timedelta(days=6)).isoformat(), endDate=top_end.isoformat(),
        metrics="views,averageViewPercentage", dimensions="video", sort="-views", maxResults=5,
    ).execute()
    titles = {v["id"]: v["snippet"]["title"] for v in videos}
    top_videos = [(titles.get(r[0], r[0]), int(r[1]), float(r[2])) for r in top.get("rows", [])]

    todo_path = PROJECT_ROOT / "todo.md"
    open_todos = open_todos_from_markdown(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []

    return format_report(
        channel_title=ch["snippet"]["title"],
        now=now,
        current_subs=int(stats.get("subscriberCount", 0)),
        total_views=int(stats.get("viewCount", 0)),
        video_count=int(stats.get("videoCount", 0)),
        days=days,
        data_through=data_through,
        published_this_week=published,
        stock_count=stock_count,
        stock_until=stock_until,
        top_videos=top_videos,
        commits=_git_commits(),
        open_todos=open_todos,
    )


def _print_cloud_env() -> None:
    """クラウド環境に登録する環境変数の値を表示する（--auth 用）。"""
    get_credentials()
    tok = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    print("クラウド環境に次の3つを環境変数として登録してください:")
    print(f"YT_CLIENT_ID={tok['client_id']}")
    print(f"YT_CLIENT_SECRET={tok['client_secret']}")
    print(f"YT_REFRESH_TOKEN={tok['refresh_token']}")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    if "--auth" in sys.argv:
        _print_cloud_env()
    else:
        print(build_report())
