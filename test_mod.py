"""mod_shot_logger の動作確認: WoT でリプレイを再生して shot_events.json を検証する。"""

import json
import time
from pathlib import Path

from src.launcher import launch_replay, wait_for_replay_start, wait_for_replay_end, kill_wot

REPLAY = Path(r"G:\その他のパソコン\マイ コンピュータ\replays\20260619_1904_japan-J28_O_I_100_Arcade_31_airfield.wotreplay")
SHOT_LOG = Path(r"C:\Games\World_of_Tanks_ASIA\shot_events.json")

# 前回の残留ファイルをクリア
if SHOT_LOG.exists():
    SHOT_LOG.unlink()
    print("前回の shot_events.json を削除しました")

print(f"リプレイ起動: {REPLAY.name}")
proc, log_offset = launch_replay(REPLAY)

print("バトル開始を待機中...")
battle_offset = wait_for_replay_start(log_offset, timeout=120)
if not battle_offset:
    kill_wot()
    print("ERROR: バトル開始を検出できませんでした")
    raise SystemExit(1)

print(f"バトル開始検出。shot_events.json の生成を監視します...")

# バトル開始後 10 秒待って mod が起動しているか確認
time.sleep(10)
if SHOT_LOG.exists():
    data = json.loads(SHOT_LOG.read_text())
    print(f"[OK] shot_events.json 生成済み: arena_start={data.get('arena_start')}")
else:
    print("[WARN] shot_events.json がまだありません（mod が読み込まれていない可能性）")

print("リプレイ終了を待機中（最大 15 分）...")
wait_for_replay_end(battle_offset, timeout=900)

kill_wot()
print("WoT 終了")

if SHOT_LOG.exists():
    data = json.loads(SHOT_LOG.read_text())
    events = data.get("events", [])
    print(f"\n=== 結果 ===")
    print(f"arena_start : {data.get('arena_start')}")
    print(f"ショット数  : {len(events)}")
    for e in events:
        print(f"  t={e['t']:7.3f}s  type={e['type']}")
else:
    print("\n[FAIL] shot_events.json が生成されませんでした")
    print("python.log を確認してください: C:\\Games\\World_of_Tanks_ASIA\\python.log")
