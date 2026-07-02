"""
claude CLI（ヘッドレスモード `claude -p`）を使った動画タイトル生成。

API キー不要・サブスクリプション使用量のみで動く。
claude CLI が無い・失敗した場合は None を返し、呼び出し側が
テンプレートタイトル（parse_replay.generate_title）にフォールバックする。
"""

import subprocess

from src.parse_replay import BattleInfo, _vehicle_display_name

MAX_TITLE_LEN = 100  # YouTube のタイトル上限
REQUIRED_TAGS = ["#Shorts", "#WorldOfTanks"]
CLAUDE_TIMEOUT_SEC = 90


def build_prompt(info: BattleInfo) -> str:
    """戦績情報から claude への指示プロンプトを組み立てる。"""
    s = info.player_stats
    result = "勝利" if info.player_won else "敗北"
    survived = "生存" if s.survived else "撃破された"
    return (
        "World of Tanks の戦闘動画（YouTube Shorts）のタイトルを1つ生成してください。\n"
        "\n"
        "戦績:\n"
        f"- 車両: {_vehicle_display_name(info.player_vehicle)}\n"
        f"- マップ: {info.map_name}\n"
        f"- 結果: {result}（{survived}）\n"
        f"- キル数: {s.kills}\n"
        f"- 与ダメージ: {s.damage_dealt}\n"
        f"- 命中率: {s.hit_rate:.0%}（{s.direct_hits}/{s.shots}）\n"
        f"- 偵察: {s.spotted}両発見\n"
        "\n"
        "条件:\n"
        "- 日本語で、視聴者がクリックしたくなる具体的で誇張しすぎないタイトル\n"
        "- 車両名を含める\n"
        f"- 末尾に {' '.join(REQUIRED_TAGS)} を付ける\n"
        f"- 全体で{MAX_TITLE_LEN}文字以内\n"
        "- タイトル本文だけを1行で出力（説明・引用符・前置きは不要）\n"
    )


def clean_title(raw: str) -> str | None:
    """
    claude の出力をタイトルとして検証・整形する。
    不正（空・複数行の説明文・長すぎ）なら None。
    """
    if not raw:
        return None
    # 最初の非空行を取る（前置きがあった場合は失敗扱いにせず先頭行を採用）
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    title = lines[0].strip().strip('"「」『』')

    if not title or len(title) > MAX_TITLE_LEN:
        return None

    # 必須ハッシュタグを保証（足りなければ追記、超過するなら None）
    missing = [t for t in REQUIRED_TAGS if t.lower() not in title.lower()]
    if missing:
        appended = f"{title} {' '.join(missing)}"
        if len(appended) > MAX_TITLE_LEN:
            return None
        title = appended
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
