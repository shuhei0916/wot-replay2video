"""
録画前プリフライトチェック。

バッチ録画の前に以下を検査し、一晩分の録画を無駄にする事故を防ぐ:
  1. OBS が起動しているか（起動していなければ自動起動）
  2. デスクトップ音声ソースの設定（ミュート・音量・録画トラック割り当て）
  3. 画面キャプチャソースの存在
  4. テスト録画: 実際に音を鳴らして数秒録画し、音声が記録されるか検証

背景: 2026-06/21〜07/02 の録画が Windows ミキサーの WoT 個別ミュートで
全て無音になり、一週間分の録画が無駄になった。
"""

import subprocess
import time
from pathlib import Path

from src.config import find_ffmpeg, find_ffprobe, load_config

_obs_cfg = load_config().get("obs", {})

OBS_EXE = Path(_obs_cfg.get(
    "exe", r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"
))
TEST_WAV = r"C:\Windows\Media\Alarm01.wav"

# 無音判定: 正常録音は ~130-190kbps、無音は ~2.3kbps
MIN_AUDIO_BITRATE = 10_000
MIN_TEST_MAX_VOLUME_DB = -40.0


def _try_connect(timeout: int = 3):
    """OBS WebSocket への接続を試みる。失敗したら None。"""
    try:
        import obsws_python as obs
        return obs.ReqClient(
            host=_obs_cfg.get("host", "localhost"),
            port=_obs_cfg.get("port", 4455),
            password=_obs_cfg.get("password", ""),
            timeout=timeout,
        )
    except Exception:
        return None


def ensure_obs_running():
    """
    OBS に接続できなければ起動し、WebSocket 接続できるまで待つ。

    Returns:
        接続済みの ReqClient

    Raises:
        RuntimeError: 起動・接続に失敗した場合
    """
    client = _try_connect()
    if client is not None:
        return client

    if not OBS_EXE.exists():
        raise RuntimeError(f"OBS が見つかりません: {OBS_EXE}")

    print(f"OBS を起動します: {OBS_EXE}")
    # OBS は作業ディレクトリが bin/64bit でないと起動に失敗する
    subprocess.Popen(
        [str(OBS_EXE), "--disable-shutdown-check", "--minimize-to-tray"],
        cwd=str(OBS_EXE.parent),
    )

    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(3)
        client = _try_connect()
        if client is not None:
            print("OBS WebSocket に接続しました")
            return client
    raise RuntimeError("OBS を起動しましたが WebSocket に接続できません")


def check_audio_settings(client) -> list[str]:
    """デスクトップ音声の設定を検査し、問題のリストを返す（空なら正常）。"""
    problems = []
    try:
        sp = client.send("GetSpecialInputs", {})
        desktop = getattr(sp, "desktop1", None)
    except Exception as e:
        return [f"特殊入力を取得できません: {e}"]

    if not desktop:
        return ["デスクトップ音声ソースが設定されていません"]

    try:
        if client.get_input_mute(desktop).input_muted:
            problems.append(f"「{desktop}」がミュートされています")
        vol = client.get_input_volume(desktop)
        if vol.input_volume_mul < 0.5:
            problems.append(
                f"「{desktop}」の音量が低すぎます ({vol.input_volume_db:.1f} dB)"
            )
        tracks = client.send(
            "GetInputAudioTracks", {"inputName": desktop}
        ).input_audio_tracks
        if not tracks.get("1", False):
            problems.append(f"「{desktop}」が録画トラック1に割り当てられていません")
    except Exception as e:
        problems.append(f"「{desktop}」の設定を検査できません: {e}")

    return problems


def check_video_source(client) -> list[str]:
    """画面キャプチャ系ソースが存在するか検査する。"""
    capture_kinds = ("monitor_capture", "window_capture", "game_capture")
    try:
        inputs = client.get_input_list().inputs
    except Exception as e:
        return [f"入力一覧を取得できません: {e}"]
    for i in inputs:
        if any(k in i["inputKind"] for k in capture_kinds):
            return []
    return ["画面キャプチャソースが見つかりません"]


def _audio_stats(video_path: str) -> tuple[int | None, float | None]:
    """録画ファイルの音声ビットレートと最大音量(dB)を返す。"""
    bitrate = None
    max_vol = None

    ffprobe = find_ffprobe()
    if ffprobe:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=bit_rate",
             "-select_streams", "a:0", "-of", "csv=p=0", video_path],
            capture_output=True, text=True,
        )
        try:
            bitrate = int(r.stdout.strip())
        except ValueError:
            pass

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        r = subprocess.run(
            [ffmpeg, "-i", video_path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, errors="replace",
        )
        for line in r.stderr.splitlines():
            if "max_volume" in line:
                try:
                    max_vol = float(line.split(":")[1].strip().split(" ")[0])
                except (IndexError, ValueError):
                    pass

    return bitrate, max_vol


def test_recording(client) -> list[str]:
    """
    テスト音を鳴らしながら数秒録画し、音声が実際に記録されるか検証する。
    録画したテストファイルは削除する。
    """
    import winsound

    client.start_record()
    time.sleep(1)
    try:
        winsound.PlaySound(TEST_WAV, winsound.SND_FILENAME | winsound.SND_ASYNC)
        time.sleep(5)
    finally:
        winsound.PlaySound(None, winsound.SND_PURGE)
        resp = client.stop_record()

    test_file = Path(resp.output_path)
    time.sleep(3)  # OBS の書き終わり待ち

    bitrate, max_vol = _audio_stats(str(test_file))

    try:
        test_file.unlink()
    except OSError:
        pass

    problems = []
    if bitrate is None and max_vol is None:
        problems.append("テスト録画の音声を解析できません（ffmpeg/ffprobe 未検出？）")
    if bitrate is not None and bitrate < MIN_AUDIO_BITRATE:
        problems.append(
            f"テスト録画が無音です (audio bitrate={bitrate} bps)。"
            "Windows ミキサーのアプリ個別ミュート等を確認してください"
        )
    if max_vol is not None and max_vol < MIN_TEST_MAX_VOLUME_DB:
        problems.append(
            f"テスト録画の音量が異常に低いです (max {max_vol:.1f} dB)"
        )
    return problems


def run_preflight() -> bool:
    """
    プリフライトチェックを実行し、録画可能な状態なら True を返す。
    問題があれば内容を表示して False を返す。
    """
    print("=== 録画プリフライトチェック ===")
    try:
        client = ensure_obs_running()
    except RuntimeError as e:
        print(f"[NG] {e}")
        return False

    problems = []
    problems += check_audio_settings(client)
    problems += check_video_source(client)

    if not problems:
        print("設定検査 OK。テスト録画を実行します...")
        problems += test_recording(client)

    client.disconnect()

    if problems:
        for p in problems:
            print(f"[NG] {p}")
        return False

    print("[OK] プリフライトチェック合格")
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_preflight() else 1)
