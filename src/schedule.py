"""
予約投稿（YouTube status.publishAt）のスロット割り当て。

config の youtube.schedule.times（JST の "HH:MM" リスト）を1日の公開スロットとし、
「最後尾の予約日時」を output/schedule_state.json に記録しながら次の空きスロットへ
詰めていく。過去時刻を publishAt に渡すと即時公開になるため、now + リード時間より
前のスロットは使わない。state が消えても now から自己修復する。

使い方（アップロード側）:
    publish_at = allocate_publish_at()          # None なら予約なし（即時公開）
    ... upload_video(..., publish_at=publish_at)
    commit_publish_at(publish_at)               # 成功時のみ呼ぶ（スロット消費を確定）
"""

import datetime
import json
from pathlib import Path

from src.config import OUTPUT_DIR, load_config

STATE_PATH = OUTPUT_DIR / "schedule_state.json"

JST = datetime.timezone(datetime.timedelta(hours=9))
UTC = datetime.timezone.utc

# publishAt が「過去」にならないための余裕。アップロード所要時間も見込む
MIN_LEAD = datetime.timedelta(minutes=15)


def parse_times(times: list[str]) -> list[datetime.time]:
    """config の "HH:MM" リストを time のソート済みリストにする（重複除去）。"""
    parsed = {
        datetime.time(int(h), int(m))
        for h, m in (t.split(":", 1) for t in times)
    }
    return sorted(parsed)


def next_slot(
    last_scheduled: datetime.datetime | None,
    now: datetime.datetime,
    times: list[datetime.time],
) -> datetime.datetime:
    """
    次の公開スロット（JST aware datetime）を返す。

    条件: now + MIN_LEAD 以降、かつ last_scheduled より後の最初のスロット。

    Args:
        last_scheduled: 最後尾の予約日時（aware）。None なら未予約
        now: 現在時刻（aware）
        times: 1日の公開スロット（parse_times 済み）
    """
    if not times:
        raise ValueError("公開スロット times が空です")

    earliest = now.astimezone(JST) + MIN_LEAD
    if last_scheduled is not None:
        last_scheduled = last_scheduled.astimezone(JST)

    day = earliest.date()
    if last_scheduled is not None and last_scheduled.date() > day:
        day = last_scheduled.date()

    while True:
        for t in times:
            slot = datetime.datetime.combine(day, t, tzinfo=JST)
            if slot < earliest:
                continue
            if last_scheduled is not None and slot <= last_scheduled:
                continue
            return slot
        day += datetime.timedelta(days=1)


def to_publish_at(slot: datetime.datetime) -> str:
    """aware datetime を YouTube API の publishAt（RFC 3339 UTC）にする。"""
    return slot.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_last(state_path: Path) -> datetime.datetime | None:
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        return datetime.datetime.fromisoformat(raw["last_publish_at"])
    except (OSError, ValueError, KeyError):
        return None


def allocate_publish_at(
    now: datetime.datetime | None = None,
    state_path: Path = STATE_PATH,
) -> str | None:
    """
    次の公開スロットを RFC 3339 UTC 文字列で返す。予約投稿が無効なら None。

    state は更新しない（アップロード成功後に commit_publish_at() で確定する。
    失敗時はスロットを消費せず次の動画が同じスロットを使う）。
    """
    schedule = load_config().get("youtube", {}).get("schedule", {})
    times = schedule.get("times", [])
    if not schedule.get("enabled", False) or not times:
        return None

    if now is None:
        now = datetime.datetime.now(tz=JST)
    slot = next_slot(_load_last(state_path), now, parse_times(times))
    return to_publish_at(slot)


def commit_publish_at(publish_at: str, state_path: Path = STATE_PATH) -> None:
    """アップロード成功したスロットを最後尾として記録する。"""
    slot = datetime.datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"last_publish_at": slot.astimezone(JST).isoformat()}),
        encoding="utf-8",
    )
