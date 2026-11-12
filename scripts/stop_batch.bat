@echo off
rem 録画バッチに停止を要求する。処理中の1本（録画・編集・アップロード）が終わった時点で止まる
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist output mkdir output
type nul > output\STOP_BATCH
echo 停止を要求しました。処理中の1本が終わり次第バッチが止まります（最大15分ほど）。
"%SystemRoot%\System32\timeout.exe" /t 5
