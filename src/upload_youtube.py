"""
YouTube Data API v3 を使った動画アップロード。

テスト対象の純粋ロジック:
    extract_tags_from_title, build_video_metadata,
    is_uploaded, mark_as_uploaded, should_retry

API 通信・OAuth フローはテスト対象外。
"""

import json
import re
from pathlib import Path

# ---- 純粋ロジック（テスト対象） ----

RETRIABLE_STATUS_CODES = {500, 502, 503, 504}


def extract_tags_from_title(title: str) -> list[str]:
    """タイトル文字列中の #タグ を抽出して # なしのリストで返す。"""
    return re.findall(r"#(\w+)", title)


def build_video_metadata(
    title: str,
    privacy: str,
    category_id: str = "20",
    extra_tags: list[str] | None = None,
    localizations: dict[str, str] | None = None,
    default_language: str = "ja",
    publish_at: str | None = None,
    description: str | None = None,
) -> dict:
    """
    YouTube API の videos.insert に渡す body dict を構築する。

    Args:
        localizations: 言語コード → 翻訳タイトル。視聴者の UI 言語に応じて
            YouTube がタイトルを切り替える（例: {"en": "...", "ru": "..."}）
        default_language: メインタイトルの言語コード
        publish_at: 予約公開日時（RFC 3339 UTC）。指定時は privacy に関わらず
            private + publishAt になり、YouTube がその時刻に自動公開する
        description: 動画の説明欄（省略時は付与しない）
    """
    tags = extract_tags_from_title(title)
    if extra_tags:
        for t in extra_tags:
            if t not in tags:
                tags.append(t)

    if publish_at:
        status = {"privacyStatus": "private", "publishAt": publish_at}
    else:
        status = {"privacyStatus": privacy}

    body = {
        "snippet": {
            "title": title,
            "categoryId": category_id,
            "tags": tags,
        },
        "status": status,
    }
    if description is not None:
        body["snippet"]["description"] = description
    if localizations:
        body["snippet"]["defaultLanguage"] = default_language
        body["localizations"] = {
            lang: {"title": t, "description": ""}
            for lang, t in localizations.items()
            if lang != default_language
        }
    return body


def is_uploaded(video_stem: str, log_path: Path) -> bool:
    """アップロード済みログに video_stem が記録されているか確認する。"""
    if not log_path.exists():
        return False
    entries = json.loads(log_path.read_text(encoding="utf-8"))
    return video_stem in entries


def mark_as_uploaded(video_stem: str, log_path: Path) -> None:
    """video_stem をアップロード済みログに追記する（重複なし）。"""
    entries: list[str] = []
    if log_path.exists():
        entries = json.loads(log_path.read_text(encoding="utf-8"))
    if video_stem not in entries:
        entries.append(video_stem)
    log_path.write_text(json.dumps(sorted(entries), ensure_ascii=False, indent=2), encoding="utf-8")


def get_video_id(video_stem: str, log_path: Path) -> str | None:
    """video_stem に対応する video_id を返す（未記録なら None）。"""
    if not log_path.exists():
        return None
    entries: dict[str, str] = json.loads(log_path.read_text(encoding="utf-8"))
    return entries.get(video_stem)


def record_video_id(video_stem: str, video_id: str, log_path: Path) -> None:
    """video_stem → video_id を記録する（アップロード種別を問わない共通台帳）。"""
    entries: dict[str, str] = {}
    if log_path.exists():
        entries = json.loads(log_path.read_text(encoding="utf-8"))
    entries[video_stem] = video_id
    log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def should_retry(status_code: int, attempt: int, max_attempts: int = 3) -> bool:
    """一時的なサーバーエラーかつ試行回数が上限未満なら True を返す。"""
    return status_code in RETRIABLE_STATUS_CODES and attempt < max_attempts


_VIDEO_SUFFIX_RE = re.compile(r"_\d{8}_\d{6}(_shorts)?$")


def replay_stem_from_video(video_stem: str) -> str:
    """
    動画ファイル名からリプレイファイルの stem を復元する。
    例: 'foo_20260703_120449_shorts' → 'foo'
    """
    return _VIDEO_SUFFIX_RE.sub("", video_stem)


# ---- API 通信（テスト対象外） ----

UPLOAD_LOG = Path(__file__).parent.parent / "output" / "uploaded.json"
# video_stem → video_id の共通台帳（Shorts・ロング動画どちらのアップロードでも記録される）
VIDEO_ID_LOG = Path(__file__).parent.parent / "output" / "video_ids.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_credentials(secrets_path: Path, token_path: Path):
    """OAuth2 トークンを取得・リフレッシュする。初回はブラウザ認証が必要。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def upload_video(
    video_path: Path,
    title: str,
    privacy: str = "private",
    category_id: str = "20",
    extra_tags: list[str] | None = None,
    localizations: dict[str, str] | None = None,
    secrets_path: Path | None = None,
    token_path: Path | None = None,
    publish_at: str | None = None,
    description: str | None = None,
) -> str | None:
    """
    動画を YouTube にアップロードして動画 ID を返す。
    アップロード済みの場合は None を返す。

    Args:
        video_path: アップロードする動画ファイル
        title: 動画タイトル（#タグを含んでもよい）
        privacy: "private" / "unlisted" / "public"
        category_id: YouTube カテゴリ ID（ゲーム = "20"）
        extra_tags: タイトル外から追加するタグ
        secrets_path: client_secrets.json のパス
        token_path: token.json の保存先
        publish_at: 予約公開日時（RFC 3339 UTC）。指定時は private で上げて
            YouTube 側がその時刻に公開する
        description: 動画の説明欄
    """
    import time
    import googleapiclient.discovery
    import googleapiclient.errors
    import googleapiclient.http

    config_dir = Path(__file__).parent.parent / "config"
    secrets_path = secrets_path or config_dir / "client_secrets.json"
    token_path = token_path or config_dir / "token.json"

    stem = video_path.stem
    if is_uploaded(stem, UPLOAD_LOG):
        print(f"スキップ（アップロード済み）: {stem}")
        return None

    creds = _get_credentials(secrets_path, token_path)
    youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)

    body = build_video_metadata(
        title, privacy=privacy, category_id=category_id,
        extra_tags=extra_tags, localizations=localizations,
        publish_at=publish_at, description=description,
    )
    media = googleapiclient.http.MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=8 * 1024 * 1024,
    )

    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)

    video_id = None
    attempt = 0
    max_attempts = 3

    while video_id is None:
        try:
            status, response = request.next_chunk()
            if response is not None:
                video_id = response["id"]
        except googleapiclient.errors.HttpError as e:
            status_code = int(e.resp.status)
            attempt += 1
            if should_retry(status_code, attempt, max_attempts):
                wait = 2 ** attempt
                print(f"リトライ {attempt}/{max_attempts}（{wait}秒後）: HTTP {status_code}")
                time.sleep(wait)
            else:
                raise

    mark_as_uploaded(stem, UPLOAD_LOG)
    record_video_id(stem, video_id, VIDEO_ID_LOG)
    url = f"https://youtu.be/{video_id}"
    if publish_at:
        print(f"アップロード完了（{publish_at} に公開予約）: {url}")
    else:
        print(f"アップロード完了: {url}")
    return video_id


def update_video_description(
    video_id: str,
    description: str,
    secrets_path: Path | None = None,
    token_path: Path | None = None,
) -> None:
    """
    既存動画の説明欄を書き換える（ロング動画公開後、対応する Shorts に
    「フル試合はこちら」リンクを追記するために使う）。

    videos.update は snippet を丸ごと送る必要があるため、まず現在の
    snippet を取得してから description だけ差し替える。現状 Shorts の
    説明欄は常に空なので素朴な上書きで問題ないが、将来手動編集が入る
    場合はここでの単純上書きは要再検討。
    """
    import googleapiclient.discovery

    config_dir = Path(__file__).parent.parent / "config"
    secrets_path = secrets_path or config_dir / "client_secrets.json"
    token_path = token_path or config_dir / "token.json"

    creds = _get_credentials(secrets_path, token_path)
    youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)

    current = youtube.videos().list(part="snippet", id=video_id).execute()
    items = current.get("items", [])
    if not items:
        raise RuntimeError(f"動画が見つかりません: {video_id}")

    snippet = items[0]["snippet"]
    snippet["description"] = description
    youtube.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
