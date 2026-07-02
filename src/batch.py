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
from src.pipeline import SilentRecordingError, process_replay

DONE_LOG = OUTPUT_DIR / "processed.json"


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
    targets = [p for p in replay_paths if p.name not in done and _has_result(p)]

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

        except SilentRecordingError as e:
            # 無音はシステム的な問題（ミュート等）。続けても全滅するので中断する
            print(f"[NG] {e}")
            print("無音録画を検出したためバッチを中断します。")
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
