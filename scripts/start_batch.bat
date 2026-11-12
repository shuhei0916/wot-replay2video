@echo off
rem 録画バッチを開始する（出かける前にダブルクリック）
rem 止めるときは stop_batch.bat（処理中の1本が終わったら停止）
chcp 65001 >nul
title wot-replay2video batch
set PYTHONUTF8=1
cd /d "%~dp0.."
if exist output\STOP_BATCH del output\STOP_BATCH
".venv\Scripts\python.exe" -u run_batch.py
echo.
echo バッチが終了しました。このウィンドウは閉じて構いません。
pause
