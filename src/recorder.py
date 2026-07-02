"""
OBS Studio の WebSocket API を使って WoT 画面を録画する。

セットアップ:
  1. OBS Studio をインストール（https://obsproject.com/）
  2. OBS を起動 → ツール → obs-websocket 設定
       「WebSocket サーバーを有効にする」にチェック
       サーバーポート: 4455
       パスワードを設定し、config.yaml の obs.password に記載する
  3. OBS 側でシーン・映像ソース・音声を設定する
     （映像: 画面キャプチャ、音声: デスクトップ音声）
  4. pip install obsws-python
"""

import shutil
import time
from pathlib import Path

try:
    import obsws_python as obs
except ImportError:
    obs = None  # type: ignore

try:
    import yaml
    _cfg_path = Path(__file__).parent.parent / "config.yaml"
    _cfg = yaml.safe_load(_cfg_path.read_text(encoding="utf-8")) if _cfg_path.exists() else {}
except Exception:
    _cfg = {}

_obs_cfg = _cfg.get("obs", {})

OUTPUT_DIR = Path(__file__).parent.parent / "output"

OBS_HOST = _obs_cfg.get("host", "localhost")
OBS_PORT = _obs_cfg.get("port", 4455)
OBS_PASSWORD = _obs_cfg.get("password", "")


def _get_client() -> "obs.ReqClient":
    if obs is None:
        raise ImportError(
            "obsws-python が必要です: pip install obsws-python"
        )
    try:
        return obs.ReqClient(
            host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD, timeout=10
        )
    except Exception as e:
        raise RuntimeError(
            f"OBS に接続できません: {e}\n"
            "OBS Studio が起動中か確認してください。\n"
            "ツール → obs-websocket 設定 → 「WebSocket サーバーを有効にする」"
        ) from e


# リプレイ録画中にミュートするマイク入力ソース名
_MIC_SOURCE = _obs_cfg.get("mic_source", "マイク")


def start_recording() -> "obs.ReqClient":
    """
    OBS の録画を開始する。マイク入力はリプレイ録画に不要なためミュートする。

    Returns:
        OBS クライアント（stop_recording() に渡す）
    """
    client = _get_client()
    try:
        client.set_input_mute(_MIC_SOURCE, True)
        print(f"マイクをミュート: {_MIC_SOURCE}")
    except Exception:
        pass  # マイクソースが存在しない場合は無視
    client.start_record()
    print("OBS 録画開始")
    return client


def stop_recording(
    client: "obs.ReqClient",
    output_path: Path | None = None,
) -> Path:
    """
    OBS の録画を停止し、録画ファイルのパスを返す。

    Args:
        client: start_recording() が返したクライアント
        output_path: ファイルの移動先パス（None なら OBS のデフォルト保存先）

    Returns:
        録画ファイルのパス
    """
    resp = client.stop_record()
    try:
        client.set_input_mute(_MIC_SOURCE, False)
    except Exception:
        pass
    client.disconnect()

    # OBS が返すパスは Windows パス（例: C:\Users\...\recording.mp4）
    recorded = Path(resp.output_path)
    print(f"OBS 録画停止: {recorded}")

    if output_path is not None and recorded != output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # OBS が MP4 を書き終えるまで待つ（最大 15 秒リトライ）
        for attempt in range(15):
            try:
                shutil.move(str(recorded), str(output_path))
                return output_path
            except PermissionError:
                time.sleep(1)
        raise PermissionError(f"録画ファイルを移動できません（OBS がファイルを保持中）: {recorded}")

    return recorded
