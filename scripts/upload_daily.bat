@echo off
rem タスクスケジューラから毎日呼ばれる: アップロード待ちの Shorts を予約投稿で上げる
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist output\logs mkdir output\logs
".venv\Scripts\python.exe" -u upload_backlog.py >> output\logs\upload_daily.log 2>&1
rem 人気 WoT Shorts のタイトル収集（将来のタイトル生成の手本用。失敗してもアップロードには影響しない）
".venv\Scripts\python.exe" -u -m src.collect_titles >> output\logs\collect_titles.log 2>&1
