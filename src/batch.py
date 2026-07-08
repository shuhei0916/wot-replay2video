"""
複数の .wotreplay を順番に処理して Shorts 動画を生成する。

使い方:
    python -m src.batch                          # replays/ フォルダを処理
    python -m src.batch path/to/replay1.wotreplay path/to/replay2.wotreplay
"""

import json
import struct
import sys
import traceback
from pathlib import Path

from src.config import OUTPUT_DIR, load_config
from src.parse_replay import PlayerStats, parse_replay
from src.pipeline import RecordingEnvironmentError, SilentRecordingError, process_replay

DONE_LOG = OUTPUT_DIR / "processed.json"

# 録画価値フィルタのデフォルト基準（いずれかを満たせば録画する）
DEFAULT_MIN_KILLS = 2
DEFAULT_MIN_DAMAGE = 1500
DEFAULT_MIN_MASTERY = 3  # 3 = 1級, 4 = M章


def meets_criteria(
    stats: PlayerStats,
    min_kills: int = DEFAULT_MIN_KILLS,
    min_damage: int = DEFAULT_MIN_DAMAGE,
    min_mastery: int = DEFAULT_MIN_MASTERY,
) -> bool:
    """Shorts の見せ場が期待できる成績か（OR 条件）。"""
    return (
        stats.kills >= min_kills
        or stats.damage_dealt >= min_damage
        or stats.mark_of_mastery >= min_mastery
    )


def _select_worthy(
    paths: list[Path],
    min_kills: int | None = None,
    min_damage: int | None = None,
    min_mastery: int | None = None,
) -> list[Path]:
    """
    録画する価値のあるリプレイだけに絞る。

    録画は1本 ~10 分かかるため、Block 2 の戦闘結果メタデータで
    事前に判定する。解析できないもの（戦闘結果なし = 途中退出等）は除外。
    閾値の未指定分は config.yaml の batch: セクション、次いでデフォルト値。
    """
    cfg = load_config().get("batch", {})
    if not cfg.get("filter_enabled", True):
        return paths

    if min_kills is None:
        min_kills = cfg.get("min_kills", DEFAULT_MIN_KILLS)
    if min_damage is None:
        min_damage = cfg.get("min_damage", DEFAULT_MIN_DAMAGE)
    if min_mastery is None:
        min_mastery = cfg.get("min_mastery", DEFAULT_MIN_MASTERY)

    kept = []
    for p in paths:
        try:
            stats = parse_replay(p).player_stats
        except Exception:
            print(f"  除外（戦闘結果を解析できません）: {p.name}")
            continue
        if meets_criteria(stats, min_kills, min_damage, min_mastery):
            kept.append(p)
        else:
            print(
                f"  除外（{stats.kills}kill / {stats.damage_dealt}dmg / "
                f"M章{stats.mark_of_mastery}）: {p.name}"
            )
    return kept


def _has_result(path: Path) -> bool:
    """Block 2（試合結果）が存在するリプレイかどうかを確認する。"""
    try:
        # ヘッダ 8 バイトで判定できる（Drive 上のファイルの全読みを避ける）
        with open(path, "rb") as f:
            head = f.read(8)
        num_blocks = struct.unpack_from("<I", head, 4)[0]
        return num_blocks >= 2
    except Exception:
        return False


def _load_done() -> set[str]:
    if DONE_LOG.exists():
        return set(json.loads(DONE_LOG.read_text(encoding="utf-8")))
    return set()


def _save_done(done: set[str]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DONE_LOG.write_text(json.dumps(sorted(done), ensure_ascii=False, indent=2), encoding="utf-8")


def process_replays(replay_paths: list[Path], preflight: bool = True) -> None:
    done = _load_done()
    candidates = [p for p in replay_paths if p.name not in done and _has_result(p)]

    print(f"録画価値を判定中: {len(candidates)} 本...")
    targets = _select_worthy(candidates)
    skipped = len(candidates) - len(targets)
    if skipped:
        print(f"成績基準未満のため {skipped} 本をスキップ（録画対象: {len(targets)} 本）")

    if not targets:
        print("処理対象のリプレイがありません。")
        return

    if preflight:
        from src.preflight import run_preflight
        if not run_preflight():
            print("プリフライトチェックに失敗したためバッチを中止します。")
            return

    print(f"処理対象: {len(targets)} 本")
    for i, replay in enumerate(targets, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(targets)}] {replay.name}")
        print(f"{'='*60}")
        try:
            out_path = process_replay(replay)

            done.add(replay.name)
            _save_done(done)
            print(f"[OK] 完了: {out_path.name}")

        except (SilentRecordingError, RecordingEnvironmentError) as e:
            # システム的な問題（ミュート・前面化失敗等）。続けても全滅するので中断する
            print(f"[NG] {e}")
            print("録画環境の異常を検出したためバッチを中断します。")
            break
        except Exception as e:
            print(f"[NG] エラー（スキップ）: {e}")
            traceback.print_exc()

    print(f"\n全処理完了。{len(done)} 本が処理済みです。")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        replay_dir = Path(__file__).parent.parent / "replays"
        paths = sorted(replay_dir.glob("*.wotreplay"))
        if not paths:
            # config.yaml の wot.dir/replays も探す
            wot_dir = Path(load_config().get("wot", {}).get("dir", ""))
            if wot_dir.name:
                paths = sorted((wot_dir / "replays").glob("*.wotreplay"))

    if not paths:
        print("使い方: python -m src.batch <replay1.wotreplay> [replay2 ...]")
        sys.exit(1)

    process_replays(paths)
