"""
claude CLI（ヘッドレスモード `claude -p`）を使った動画タイトル生成。

API キー不要・サブスクリプション使用量のみで動く。
claude CLI が無い・失敗した場合は None を返し、呼び出し側が
テンプレートタイトル（parse_replay.generate_title）にフォールバックする。
"""

import re
import subprocess

from src.achievements import notable_medals
from src.parse_replay import BattleInfo, _vehicle_display_name

MAX_TITLE_LEN = 100  # YouTube のタイトル上限
CLAUDE_TIMEOUT_SEC = 90

# 多言語タイトルの生成対象（ja は必須。他は取れた分だけ localizations に使う）
LOCALIZE_LANGS = ("en", "ru")

_TITLE_MARKER_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_LANG_MARKER_RE = re.compile(r"<(ja|en|ru)>(.*?)</\1>", re.DOTALL)

# 前置き・メタ文の兆候（マーカーが無いときのフォールバック判定に使う）
_META_MARKERS = ("タイトル", "生成", "以下", "案:", "出力", "承知", "了解")


def _battle_facts(info: BattleInfo) -> str:
    """プロンプトに渡す戦績サマリー。タイトルに使う情報だけに絞る。"""
    s = info.player_stats
    result = "勝利" if info.player_won else "敗北"
    survived = "生存" if s.survived else "撃破された"
    lines = [
        f"- 車両: {_vehicle_display_name(info.player_vehicle)}",
        f"- マップ: {info.map_name}",
        f"- 結果: {result}（{survived}）",
        f"- キル数: {s.kills}",
        f"- 与ダメージ: {s.damage_dealt}",
    ]
    medals = notable_medals(s.achievements)
    if s.mark_of_mastery == 4:
        medals.append("マスターバッジ「エース」")
    if medals:
        lines.append(f"- 獲得した勲章: {'、'.join(medals)}")
    return "\n".join(lines)


def _style_rules(info: BattleInfo) -> str:
    """タイトルのスタイル指示（日本語基準）。"""
    has_medal = bool(notable_medals(info.player_stats.achievements))
    medal_rule = (
        "- 勲章を獲得しているので、その勲章名を必ずタイトルに入れる（レアな見せ場の証明）\n"
        if has_medal
        else ""
    )
    return (
        "- 車両名を必ず入れる\n"
        + medal_rule
        + "- 短くシンプルに（車両名・勲章名を除いて15文字前後）。口語で、"
        "人がぽろっとつぶやいた一言の温度感\n"
        "- 例の雰囲気: 「O-I 100、今日は当たる日」「T95 でコロバノフ勲章きた」\n"
        "- 命中率などの細かい数字は書かない。数字を入れるなら多くて1つ"
        "（キル数か与ダメのどちらか、それが見どころのときだけ）\n"
        "- 絵文字は合うときだけ最大1個（毎回は付けない）\n"
        "- ハッシュタグ不要\n"
    )


def build_prompt(info: BattleInfo) -> str:
    """戦績情報から claude への指示プロンプトを組み立てる。"""
    return (
        "World of Tanks の実況なし戦闘動画（YouTube Shorts）のタイトルを1つ考えてください。\n"
        "\n"
        "戦績:\n"
        f"{_battle_facts(info)}\n"
        "\n"
        "スタイル:\n"
        f"{_style_rules(info)}"
        "\n"
        "出力は <title>タイトル</title> の形式で、タイトルだけを書いてください。\n"
        "前置き・説明・複数案は不要です。\n"
    )


def build_multilang_prompt(info: BattleInfo) -> str:
    """日本語 + 英語 + ロシア語のタイトルを一度に生成する指示プロンプト。"""
    return (
        "World of Tanks の実況なし戦闘動画（YouTube Shorts）のタイトルを、\n"
        "日本語・英語・ロシア語で1つずつ考えてください。\n"
        "\n"
        "戦績:\n"
        f"{_battle_facts(info)}\n"
        "\n"
        "スタイル（3言語とも共通、字数は日本語基準）:\n"
        f"{_style_rules(info)}"
        "- 英語・ロシア語は直訳ではなく、その言語で自然な同じ温度感の一言にする"
        "（勲章名は各言語の公式名: 例 medalKolobanov → Kolobanov's Medal / медаль Колобанова）\n"
        "\n"
        "出力は次の3行だけ（前置き・説明は不要）:\n"
        "<ja>日本語タイトル</ja>\n"
        "<en>English title</en>\n"
        "<ru>Русский заголовок</ru>\n"
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

    return _sanitize(title)


def _sanitize(title: str) -> str | None:
    """1言語分のタイトルを検証・整形する。"""
    title = title.strip().strip('"「」『』')
    if not title or "\n" in title or len(title) > MAX_TITLE_LEN:
        return None
    return title


def parse_multilang_titles(raw: str) -> dict[str, str] | None:
    """
    <ja>...</ja> <en>...</en> <ru>...</ru> 形式の出力を辞書にする。
    ja が取れなければ None（en/ru は取れた分だけ）。
    """
    if not raw:
        return None
    titles: dict[str, str] = {}
    for m in _LANG_MARKER_RE.finditer(raw):
        cleaned = _sanitize(m.group(2))
        if cleaned:
            titles[m.group(1)] = cleaned
    if "ja" not in titles:
        return None
    return titles


def _run_claude(prompt: str, timeout_sec: int) -> str | None:
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
    return r.stdout


def generate_title_llm(info: BattleInfo, timeout_sec: int = CLAUDE_TIMEOUT_SEC) -> str | None:
    """claude -p で日本語タイトルを生成する。失敗したら None。"""
    out = _run_claude(build_prompt(info), timeout_sec)
    return clean_title(out) if out else None


def generate_titles_llm(
    info: BattleInfo, timeout_sec: int = CLAUDE_TIMEOUT_SEC
) -> dict[str, str] | None:
    """
    claude -p で多言語タイトル（ja 必須 + en/ru）を一度に生成する。
    失敗したら None（フォールバックは呼び出し側）。
    """
    out = _run_claude(build_multilang_prompt(info), timeout_sec)
    return parse_multilang_titles(out) if out else None
