"""
リプレイ再生 + 録画 + Shorts 生成 + アップロードのパイプライン。

各ステージは独立した関数で、process_replay() がオーケストレーションする:
  record_replay()          リプレイを再生して録画（seekable MP4）
  make_highlight_shorts()  ハイライト検出 → Shorts 生成
  build_title()            リプレイ情報からタイトル生成
  upload_shorts()          YouTube アップロード（失敗しても継続）
"""

import datetime
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from src.config import OUTPUT_DIR, find_ffmpeg, find_ffprobe, load_config, wot_dir
from src.launcher import (
    bring_wot_to_foreground,
    is_wot_foreground,
    kill_wot,
    launch_replay,
    wait_for_replay_end,
    wait_for_replay_start,
)
from src.recorder import start_recording, stop_recording
from src.detect_highlights import detect_highlights
from src.edit_video import make_shorts
from src.parse_replay import parse_replay, generate_title
from src.upload_youtube import upload_video


class SilentRecordingError(RuntimeError):
    """録画の音声が無音だった（システム的な問題なのでバッチは中断すべき）。"""


class RecordingEnvironmentError(RuntimeError):
    """録画環境の異常（WoT が前面に出せない等）。バッチは中断すべき。"""


# 正常録音は ~130-190kbps、無音録画は ~2.3kbps
MIN_AUDIO_BITRATE = 10_000

# mod (mod_shot_logger) のイベント出力先。次のバトルで上書きされるため
# 録画ごとに <recording>.events.json へスナップショットする
SHOT_EVENTS_PATH = wot_dir() / "shot_events.json"


def _audio_bitrate(video_path: Path) -> int | None:
    """録画の音声ビットレートを返す。検査できない場合は None。"""
    ffprobe = find_ffprobe()
    if ffprobe is None:
        return None
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=bit_rate",
         "-select_streams", "a:0", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _remux_faststart(src: Path) -> Path:
    """mp4 を上書きリムックスして moov アトムを先頭に移動する（seekable 化）。"""
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        print("警告: ffmpeg が見つかりません。シーク不可のまま出力します。")
        return src

    tmp = src.with_suffix(".tmp.mp4")
    # OBS のファイナライズ完了直後は moov atom がまだ無いことがあるためリトライ
    for attempt in range(3):
        r = subprocess.run(
            [ffmpeg, "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(tmp), "-y"],
            capture_output=True,
        )
        if r.returncode == 0:
            tmp.replace(src)
            return src
        if attempt < 2:
            print(f"リムックス失敗（{attempt + 1}/3）。5秒後にリトライします...")
            time.sleep(5)
    raise RuntimeError(
        f"リムックスに失敗しました: {src}\n{r.stderr.decode(errors='replace')[-500:]}"
    )


def record_replay(replay_path: Path) -> Path:
    """
    リプレイを再生しながら OBS で録画して、動画ファイルパスを返す。

    録画の解像度・フレームレート・音声は OBS 側で設定する。

    Args:
        replay_path: .wotreplay ファイルの Windows パス

    Returns:
        録画された動画ファイルのパス（seekable MP4）
    """
    replay_path = Path(replay_path).resolve()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"{replay_path.stem}_{ts}.mp4"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 前バトルの mod イベント残骸をクリア（存在＝今回のセッションの出力と保証する）
    try:
        SHOT_EVENTS_PATH.unlink()
    except OSError:
        pass

    print(f"[1/5] WoT 起動中: {replay_path.name}")
    wot_proc, launched_at = launch_replay(replay_path)

    try:
        print("[2/5] リプレイ開始を待機中...")
        battle_log_offset = wait_for_replay_start(launched_at, timeout=300)
        if not battle_log_offset:
            raise TimeoutError("リプレイ開始の検出がタイムアウトしました")

        # OBS が monitor_capture の場合は WoT が前面にいないと別の画面が
        # 録画される（2026-07-03 の事故）。window_capture なら前面は不要
        # だが、確実な描画のため前面化は試みる（失敗しても続行）
        capture_mode = load_config().get("obs", {}).get("capture", "monitor")
        fg_ok = bring_wot_to_foreground()
        if not fg_ok and capture_mode != "window":
            raise RecordingEnvironmentError(
                "WoT ウィンドウを前面に出せません。録画すると別の画面が映るため中断します"
            )

        print(f"[3/5] 録画開始 → {out_path.name}")
        rec_client = start_recording()
        rec_start_epoch = time.time()

        if capture_mode != "window" and not is_wot_foreground():
            raise RecordingEnvironmentError(
                "録画開始時に WoT が前面にありません。中断します"
            )

        print("[4/5] リプレイ終了を待機中...")
        if not wait_for_replay_end(battle_log_offset, timeout=900):
            print("警告: リプレイ終了の検出がタイムアウトしました（強制終了）")

        print("[5/5] 録画停止・WoT 終了...")
        stop_recording(rec_client, out_path)
    finally:
        kill_wot()

    print("リムックス中（seekable 化）...")
    _remux_faststart(out_path)

    # 無音録画ガード: 無音ならシステム的な問題（ミュート等）なので即座に検出する
    bitrate = _audio_bitrate(out_path)
    if bitrate is not None and bitrate < MIN_AUDIO_BITRATE:
        raise SilentRecordingError(
            f"録画が無音です (audio bitrate={bitrate} bps): {out_path}\n"
            "Windows ミキサーの WoT 個別ミュート・OBS の音声設定を確認してください"
        )

    # サイドカー保存: 録画開始 epoch と mod イベントのスナップショット。
    # これがあれば後からでも mod ベースのハイライト検出を再実行できる
    out_path.with_suffix(".meta.json").write_text(
        json.dumps({"rec_start_epoch": rec_start_epoch, "replay": replay_path.name}),
        encoding="utf-8",
    )
    if SHOT_EVENTS_PATH.exists():
        shutil.copy(str(SHOT_EVENTS_PATH), str(out_path.with_suffix(".events.json")))
        print(f"mod イベントを保存: {out_path.with_suffix('.events.json').name}")
    else:
        print("mod イベントなし（mod 未配置または未発火。CV 検出にフォールバックします）")

    return out_path


def _detect_events(recording_path: Path):
    """
    ハイライトイベント検出。優先順位: mod > 音声+輝度融合 > 輝度のみ。

    mod イベント（サイドカー .meta.json / .events.json）は推測を含まない
    正確な射撃記録なので、存在すれば最優先で使い、音声ピークでスコア付けする。
    """
    audio = []
    try:
        from src.detect_audio_events import detect_audio_events, fuse_events
        audio = detect_audio_events(recording_path, skip_initial_sec=40.0)
    except Exception as e:
        print(f"警告: 音声解析に失敗: {e}")
        fuse_events = None

    from src.detect_mod_events import load_mod_events, score_with_audio
    mod_events = load_mod_events(recording_path)
    if mod_events:
        print(f"  mod イベント {len(mod_events)} 件を使用（音声ピーク {len(audio)} 件でスコア付け）")
        return score_with_audio(mod_events, audio)

    flash = detect_highlights(recording_path)
    if audio and fuse_events is not None:
        print(f"  音声ピーク {len(audio)} 件 / 輝度フラッシュ {len(flash)} 件を融合")
        return fuse_events(flash, audio)
    return flash


def make_highlight_shorts(recording_path: Path) -> Path | None:
    """
    録画からハイライトを検出して Shorts 動画を生成する。

    Returns:
        Shorts 動画のパス。ハイライトが見つからない場合は None。
    """
    print("ハイライト検出中...")
    events = _detect_events(recording_path)
    print(f"  {len(events)} 件のショットイベントを検出")

    if not events:
        return None

    shorts_path = recording_path.with_name(recording_path.stem + "_shorts.mp4")
    print(f"Shorts 生成中 → {shorts_path.name}")
    make_shorts(recording_path, events, shorts_path)
    return shorts_path


def build_title(replay_path: Path) -> str:
    """
    リプレイのメタデータからタイトルを生成する。

    youtube.llm_title が有効なら claude -p で生成し、失敗時は
    テンプレート生成、解析自体の失敗時はファイル名ベースに落ちる。
    """
    try:
        info = parse_replay(replay_path)
    except Exception:
        return f"【WoT】{replay_path.stem} #Shorts #WorldOfTanks"

    if load_config().get("youtube", {}).get("llm_title", False):
        from src.llm_title import generate_title_llm
        title = generate_title_llm(info)
        if title:
            return title
        print("警告: LLM タイトル生成に失敗。テンプレートにフォールバックします")

    return generate_title(info)


def upload_shorts(video_path: Path, title: str) -> None:
    """Shorts を YouTube にアップロードする。失敗してもパイプラインは継続する。"""
    try:
        yt = load_config().get("youtube", {})
        if not yt.get("enabled", True):
            print("YouTube アップロードは無効化されています (youtube.enabled: false)")
            return
        upload_video(
            video_path=video_path,
            title=title,
            privacy=yt.get("privacy", "private"),
            category_id=yt.get("category_id", "20"),
            extra_tags=yt.get("default_tags", []),
        )
    except Exception as e:
        print(f"警告: YouTube アップロードに失敗しました（動画は保持）: {e}")


def process_replay(replay_path: Path) -> Path:
    """
    1本のリプレイを録画 → Shorts 生成 → アップロードまで処理する。

    Returns:
        Shorts 動画のパス（ハイライトなしの場合は録画ファイルのパス）
    """
    replay_path = Path(replay_path)
    recording = record_replay(replay_path)

    shorts_path = make_highlight_shorts(recording)
    if shorts_path is None:
        print(f"ハイライトが見つかりませんでした。録画のみ保存: {recording}")
        return recording

    title = build_title(replay_path)
    title_path = shorts_path.with_suffix(".txt")
    title_path.write_text(title, encoding="utf-8")
    print(f"タイトル: {title}")

    upload_shorts(shorts_path, title)
    print(f"完了: {shorts_path}")
    return shorts_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        project_root = Path(__file__).parent.parent
        replays = sorted((project_root / "replays").glob("*.wotreplay"))
        if not replays:
            print("使い方: python -m src.pipeline <replay.wotreplay>")
            sys.exit(1)
        replay = replays[0]
    else:
        replay = Path(sys.argv[1])

    out = process_replay(replay)
    print(f"\n動画ファイル: {out}")
