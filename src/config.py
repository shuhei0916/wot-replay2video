"""
config.yaml の読み込みとプロジェクト共有のパス・ユーティリティ。

各モジュールが個別に config.yaml を読むとエンコーディング指定漏れ等の
バグが分散するため、読み込みはここに集約する。
"""

import subprocess
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
OUTPUT_DIR = PROJECT_ROOT / "output"

# ffmpeg の探索候補。config.yaml の ffmpeg.path が最優先。
_FFMPEG_CANDIDATES = [
    "ffmpeg",
    r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe",
]


@lru_cache(maxsize=1)
def load_config() -> dict:
    """config.yaml を読み込んで dict で返す。存在しない・壊れている場合は {}。"""
    try:
        import yaml
        if CONFIG_PATH.exists():
            return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


@lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    """使用可能な ffmpeg のパスを返す。見つからなければ None。"""
    cfg_path = load_config().get("ffmpeg", {}).get("path")
    candidates = ([cfg_path] if cfg_path else []) + _FFMPEG_CANDIDATES
    for c in candidates:
        try:
            if subprocess.run([c, "-version"], capture_output=True).returncode == 0:
                return c
        except OSError:
            pass
    return None
