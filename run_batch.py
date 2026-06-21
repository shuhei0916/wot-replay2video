"""夜間バッチ処理 実行スクリプト"""
from pathlib import Path
from src.batch import process_replays

replays = [
    Path(r"G:\その他のパソコン\マイ コンピュータ\replays\20260619_1904_japan-J28_O_I_100_Arcade_31_airfield.wotreplay"),
    Path(r"G:\その他のパソコン\マイ コンピュータ\replays\20260619_1912_japan-J28_O_I_100_Arcade_99_poland.wotreplay"),
    Path(r"G:\その他のパソコン\マイ コンピュータ\replays\20260619_2029_japan-J28_O_I_100_Arcade_31_airfield.wotreplay"),
]
process_replays(replays)
