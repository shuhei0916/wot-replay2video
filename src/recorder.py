"""
WoT 画面を録画する。
Windows 側の ffmpeg (gdigrab + dshow) を WSL2 から呼び出す。

音声キャプチャには Windows の「ステレオ ミキサー」が必要。
有効化手順: サウンドコントロールパネル → 録音タブ →
           右クリック「無効なデバイスの表示」→「ステレオ ミキサー」を有効化
"""

import subprocess
import sys
import time
from pathlib import Path

# ffmpeg 候補（優先順）
FFMPEG_WIN_PATHS = [
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",         # winget install Gyan.FFmpeg
    r"C:\Program Files\Shotcut\ffmpeg.exe",            # Shotcut 同梱 (gdigrab/x264 対応)
    r"C:\Program Files\Lightworks\ffmpeg.exe",         # Lightworks 同梱
]
FFMPEG_WSL_PATHS = [
    Path("/mnt/c/Program Files/ffmpeg/bin/ffmpeg.exe"),
    Path("/mnt/c/Program Files/Shotcut/ffmpeg.exe"),
    Path("/mnt/c/Program Files/Lightworks/ffmpeg.exe"),
]

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ステレオミキサーの候補デバイス名（日本語 / 英語 Windows 両対応）
STEREO_MIX_NAMES = [
    "ステレオ ミキサー",
    "Stereo Mix",
    "Stereo mixer",
]


def find_ffmpeg() -> str | None:
    """Windows 側の ffmpeg.exe のパスを返す。見つからなければ None。"""
    for win_path, wsl_path in zip(FFMPEG_WIN_PATHS, FFMPEG_WSL_PATHS):
        if wsl_path.exists():
            return win_path
    result = subprocess.run(
        ["powershell.exe", "-Command",
         "Get-Command ffmpeg -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source"],
        capture_output=True, text=True
    )
    path = result.stdout.strip()
    return path if path else None


def find_stereo_mix(win_ffmpeg: str) -> str | None:
    """
    有効なステレオミキサーデバイス名を返す。
    dshow デバイス一覧を取得して候補名と照合する。
    見つからなければ None（音声なしで録画を続行）。
    """
    result = subprocess.run(
        ["powershell.exe", "-Command",
         f'& "{win_ffmpeg}" -list_devices true -f dshow -i dummy 2>&1'],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    output = result.stdout + result.stderr
    for name in STEREO_MIX_NAMES:
        if name in output:
            return name
    return None


def _wsl_to_win(path: Path) -> str:
    return subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True, text=True, check=True
    ).stdout.strip()


def start_recording(
    output_path: Path,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    offset_x: int = 0,
    offset_y: int = 0,
) -> subprocess.Popen:
    """
    ffmpeg gdigrab でデスクトップを録画開始する。
    ステレオミキサーが有効であれば音声も同時録音する。

    Args:
        output_path: 出力ファイルパス（WSL パス）
        fps: フレームレート
        width/height: キャプチャ領域サイズ（WoT の解像度に合わせる）
        offset_x/y: キャプチャ開始座標（マルチモニター環境でモニターを選択）

    Returns:
        ffmpeg プロセス（stop_recording() で正常終了）
    """
    win_ffmpeg = find_ffmpeg()
    if win_ffmpeg is None:
        raise RuntimeError(
            "ffmpeg.exe が見つかりません。winget install Gyan.FFmpeg で導入してください。"
        )

    win_output = _wsl_to_win(output_path)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ステレオミキサーが使えるか確認
    audio_device = find_stereo_mix(win_ffmpeg)
    if audio_device:
        print(f"音声デバイス検出: {audio_device}")
        # 音声 + 映像を同時キャプチャ
        ps_cmd = (
            f'& "{win_ffmpeg}"'
            f' -f dshow -i audio="{audio_device}"'
            f' -f gdigrab -framerate {fps}'
            f' -offset_x {offset_x} -offset_y {offset_y}'
            f' -video_size {width}x{height}'
            f' -i desktop'
            f' -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p'
            f' -c:a aac -b:a 192k'
            f' -y "{win_output}"'
        )
    else:
        print("警告: ステレオミキサーが見つかりません。映像のみ録画します。")
        print("  有効化: サウンドコントロールパネル → 録音タブ → 右クリック →")
        print("         「無効なデバイスの表示」→「ステレオ ミキサー」を有効化")
        # 映像のみ
        ps_cmd = (
            f'& "{win_ffmpeg}"'
            f' -f gdigrab -framerate {fps}'
            f' -offset_x {offset_x} -offset_y {offset_y}'
            f' -video_size {width}x{height}'
            f' -i desktop'
            f' -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p'
            f' -y "{win_output}"'
        )

    proc = subprocess.Popen(
        ["powershell.exe", "-Command", ps_cmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def stop_recording(proc: subprocess.Popen, timeout: int = 15) -> None:
    """録画を正常終了させる。ffmpeg に 'q' を送って終了させる。"""
    try:
        proc.stdin.write(b"q")
        proc.stdin.flush()
        proc.wait(timeout=timeout)
    except Exception:
        proc.kill()


if __name__ == "__main__":
    import datetime

    win_ffmpeg = find_ffmpeg()
    if win_ffmpeg is None:
        print("ffmpeg が見つかりません")
        sys.exit(1)

    audio = find_stereo_mix(win_ffmpeg)
    print(f"ffmpeg: {win_ffmpeg}")
    print(f"音声デバイス: {audio or '(なし - 映像のみ)'}")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"test_record_{ts}.mp4"

    print(f"録画開始 → {out}")
    print("Ctrl+C で停止")

    proc = start_recording(out)

    try:
        while True:
            time.sleep(1)
            if proc.poll() is not None:
                print("ffmpeg が予期せず終了しました")
                break
    except KeyboardInterrupt:
        print("\n停止中...")
        stop_recording(proc)
        print(f"録画完了: {out}")
