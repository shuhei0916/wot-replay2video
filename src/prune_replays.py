"""
再生できなくなった旧バージョンのリプレイを整理する。

WoT のリプレイは録画時と同じクライアントバージョンでしか再生できないため、
現行クライアントより古いリプレイは録画対象にならない。これをごみ箱へ送る。

- 戦績（Block 1/2 の JSON から読める成績）は消す前に output/replay_archive.jsonl へ
  1行1リプレイで書き出して残す（後から「どんな試合が伸びるか」を分析できるように）
- 完全削除ではなく Windows のごみ箱へ送る（誤削除しても戻せる）
- 現行より「新しい」バージョンは消さない（別 PC の新しいクライアントで録ったものを守る）
- バージョン不明のファイル、未アップロードの Shorts が参照するリプレイ
  （アップロード順の優先度計算に使う）は残す
- 対象は replays.dir と web.dir。佐野ラップトップの replays.dir は Google Drive の
  同期フォルダで、消すと Drive からも消えるため、config の replays.prune.enabled
  （既定 false）で PC ごとに有効化する

使い方:
    python -m src.prune_replays --dry-run   # 対象を表示するだけ
    python -m src.prune_replays             # 実行（run_batch.py も録画前に呼ぶ）
"""

import argparse
import dataclasses
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from src.config import OUTPUT_DIR, load_config, replays_dir, wot_client_version

ARCHIVE_PATH = OUTPUT_DIR / "replay_archive.jsonl"


# ---------------------------------------------------------------------------
# 純粋ロジック（テスト対象）
# ---------------------------------------------------------------------------

def prune_enabled() -> bool:
    return bool((load_config().get("replays") or {}).get("prune", {}).get("enabled", False))


def version_key(version: str) -> tuple[int, ...] | None:
    """'2.4.2.0' → (2, 4, 2, 0)。解釈できなければ None。"""
    try:
        parts = tuple(int(x) for x in version.split("."))
    except (AttributeError, ValueError):
        return None
    return parts or None


def is_obsolete(replay_version: str, client_version: str) -> bool:
    """リプレイが現行クライアントより古い（= もう再生できない）か。不明なら False。"""
    r, c = version_key(replay_version), version_key(client_version)
    return r is not None and c is not None and r < c


def pending_replay_stems(output_dir: Path, upload_log: Path) -> set[str]:
    """未アップロードの Shorts が参照するリプレイの stem。"""
    from src.upload_youtube import is_uploaded, replay_stem_from_video
    return {
        replay_stem_from_video(p.stem)
        for p in output_dir.glob("*_shorts.mp4")
        if not is_uploaded(p.stem, upload_log)
    }


def select_obsolete(
    paths: list[Path], client_version: str, keep_stems: set[str], read_version
) -> list[tuple[Path, str]]:
    """削除対象の (パス, バージョン) を返す。read_version はテスト差し替え用。"""
    out = []
    for p in paths:
        if p.stem in keep_stems:
            continue
        v = read_version(p)
        if is_obsolete(v, client_version):
            out.append((p, v))
    return out


def archive_record(path: Path, version: str) -> dict:
    """リプレイの戦績を1行分の dict にする。解析できなければ最低限の情報だけ。"""
    from src.parse_replay import parse_replay
    rec = {"file": path.name, "version": version}
    try:
        info = parse_replay(path)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec
    rec.update({
        "player_name": info.player_name,
        "vehicle": info.player_vehicle,
        "map": info.map_name,
        "region": info.region,
        "battle_time": info.battle_time.isoformat() if info.battle_time else None,
        "duration_seconds": info.duration_seconds,
        "battle_type": info.battle_type,
        "won": info.player_won,
        "stats": dataclasses.asdict(info.player_stats),
    })
    return rec


def archived_files(archive_path: Path) -> set[str]:
    names = set()
    try:
        with archive_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    names.add(json.loads(line)["file"])
                except (ValueError, KeyError):
                    pass
    except OSError:
        pass
    return names


def append_archive(records: list[dict], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# ファイル操作（テスト対象外）
# ---------------------------------------------------------------------------

_RECYCLE_PS = r"""
Add-Type -AssemblyName Microsoft.VisualBasic
$ok = 0
foreach ($p in Get-Content -LiteralPath $args[0] -Encoding UTF8) {
  if (-not $p) { continue }
  try {
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
    $ok++
  } catch { Write-Output "FAILED: $p" }
}
Write-Output "RECYCLED: $ok"
"""


def send_to_recycle_bin(paths: list[Path]) -> int:
    """まとめてごみ箱へ送る（PowerShell 1回）。送れた本数を返す。"""
    if not paths:
        return 0
    with tempfile.TemporaryDirectory() as td:
        lst = Path(td) / "list.txt"
        lst.write_text("\n".join(str(p) for p in paths), encoding="utf-8-sig")
        ps1 = Path(td) / "recycle.ps1"
        ps1.write_text(_RECYCLE_PS, encoding="utf-8-sig")
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), str(lst)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    ok = 0
    for line in r.stdout.splitlines():
        if line.startswith("FAILED: "):
            print(f"  ごみ箱へ送れませんでした: {line[8:]}")
        elif line.startswith("RECYCLED: "):
            ok = int(line[10:])
    return ok


def prune(dry_run: bool = False) -> int:
    """旧バージョンのリプレイを戦績アーカイブ後にごみ箱へ送る。送った本数を返す。"""
    from src.collect_web import web_dir
    from src.parse_replay import read_replay_version
    from src.upload_youtube import UPLOAD_LOG

    try:
        client = wot_client_version()
    except Exception as e:
        print(f"リプレイ整理: クライアントバージョンが取れないためスキップ（{e}）")
        return 0

    dirs = [d for d in dict.fromkeys([replays_dir(), web_dir()]) if d.exists()]
    paths = [p for d in dirs for p in sorted(d.glob("*.wotreplay"))]
    keep = pending_replay_stems(OUTPUT_DIR, UPLOAD_LOG)
    targets = select_obsolete(paths, client, keep, read_replay_version)

    size_gb = sum(p.stat().st_size for p, _ in targets) / 1e9
    print(f"リプレイ整理: 現行 {client}、旧バージョン {len(targets)} / {len(paths)} 本（{size_gb:.2f} GB）")
    if dry_run or not targets:
        by_ver: dict[str, int] = {}
        for _, v in targets:
            by_ver[v] = by_ver.get(v, 0) + 1
        for v, n in sorted(by_ver.items(), key=lambda x: version_key(x[0])):
            print(f"  {v}: {n} 本")
        return 0

    done = archived_files(ARCHIVE_PATH)
    new_records = [archive_record(p, v) for p, v in targets if p.name not in done]
    append_archive(new_records, ARCHIVE_PATH)
    print(f"  戦績を {ARCHIVE_PATH.name} に追記: {len(new_records)} 件")

    n = send_to_recycle_bin([p for p, _ in targets])
    print(f"  ごみ箱へ送りました: {n} 本")
    return n


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="旧バージョンのリプレイをごみ箱へ送る")
    ap.add_argument("--dry-run", action="store_true", help="対象を表示するだけ")
    args = ap.parse_args()
    if not args.dry_run and not prune_enabled():
        print("replays.prune.enabled が false のため実行しません（--dry-run は可）")
        return 0
    prune(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
