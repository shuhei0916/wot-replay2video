"""
claude CLI（ヘッドレスモード `claude -p`）を使った動画タイトル生成。

API キー不要・サブスクリプション使用量のみで動く。
claude CLI が無い・失敗した場合は None を返し、呼び出し側が
テンプレートタイトル（parse_replay.generate_title）にフォールバックする。
"""

import re
import subprocess

from src.parse_replay import BattleInfo, _vehicle_display_name

MAX_TITLE_LEN = 100  # YouTube のタイトル上限
CLAUDE_TIMEOUT_SEC = 90

_TITLE_MARKER_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)

# 前置き・メタ文の兆候（マーカーが無いときのフォールバック判定に使う）
_META_MARKERS = ("タイトル", "生成", "以下", "案:", "出力", "承知", "了解")


def build_prompt(info: BattleInfo) -> str:
    """戦績情報から claude への指示プロンプトを組み立てる。"""
    s = info.player_stats
    result = "勝利" if info.player_won else "敗北"
    survived = "生存" if s.survived else "撃破された"
    return (
        "World of Tanks の実況なし戦闘動画（YouTube Shorts）のタイトルを1つ考えてください。\n"
        "\n"
        "戦績:\n"
        f"- 車両: {_vehicle_display_name(info.player_vehicle)}\n"
        f"- マップ: {info.map_name}\n"
        f"- 結果: {result}（{survived}）\n"
        f"- キル数: {s.kills}\n"
        f"- 与ダメージ: {s.damage_dealt}\n"
        f"- 命中率: {s.hit_rate:.0%}（{s.direct_hits}/{s.shots}）\n"
        "\n"
        "スタイル:\n"
        "- 短く（20文字前後まで）。口語で、人がぽろっとつぶやいた一言のような温度感\n"
        "- 例の雰囲気: 「弾あたらん…」「今日は当たる日」「O-I 100 は正義」\n"
        "- 戦績の数字を羅列しない（1つ入れるなら効果的なものだけ）\n"
        "- 絵文字は合うときだけ最大1個（毎回は付けない）\n"
        "- ハッシュタグ不要\n"
        "\n"
        "出力は <title>タイトル</title> の形式で、タイトルだけを書いてください。\n"
        "前置き・説明・複数案は不要です。\n"
    )


def clean_title(raw: str) -> str | None:
    """
    claude の出力からタイトルを抽出・検証する。

    <title>...</title> マーカーを最優先で探す。マーカーが無い場合は
    最初の非空行を使うが、前置き文らしい行（「タイトルを生成します」等）は
    採用しない（過去にメタ文がそのままタイトルになった事故があるため）。
    """
    if not raw:
        return None

    m = _TITLE_MARKER_RE.search(raw)
    if m:
        title = m.group(1).strip()
    else:
        lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
        if not lines:
            return None
        title = lines[0]
        if any(k in title for k in _META_MARKERS):
            return None  # 前置きらしき行はタイトルとして信用しない

    title = title.strip().strip('"「」『』')
    if not title or "\n" in title or len(title) > MAX_TITLE_LEN:
        return None
    return title


def generate_title_llm(info: BattleInfo, timeout_sec: int = CLAUDE_TIMEOUT_SEC) -> str | None:
    """claude -p でタイトルを生成する。失敗したら None（フォールバックは呼び出し側）。"""
    prompt = build_prompt(info)
    try:
        r = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            timeout=timeout_sec,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return clean_title(r.stdout)
