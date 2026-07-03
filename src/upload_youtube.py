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
) -> dict:
    """YouTube API の videos.insert に渡す body dict を構築する。"""
    tags = extract_tags_from_title(title)
    if extra_tags:
        for t in extra_tags:
            if t not in tags:
                tags.append(t)

    return {
        "snippet": {
            "title": title,
            "categoryId": category_id,
            "tags": tags,
        },
        "status": {
            "privacyStatus": privacy,
        },
    }


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
    secrets_path: Path | None = None,
    token_path: Path | None = None,
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

    body = build_video_metadata(title, privacy=privacy, category_id=category_id, extra_tags=extra_tags)
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
    url = f"https://youtu.be/{video_id}"
    print(f"アップロード完了: {url}")
    return video_id
