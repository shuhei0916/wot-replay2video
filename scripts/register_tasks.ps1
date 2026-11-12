# wot-replay2video: デスクトップのショートカット作成 + 毎日のアップロードタスク登録
# 実行: PowerShell で  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell
foreach ($p in @(@('WoT録画バッチ 開始', 'start_batch.bat'), @('WoT録画バッチ 停止', 'stop_batch.bat'))) {
    $lnk = $ws.CreateShortcut((Join-Path $desktop ($p[0] + '.lnk')))
    $lnk.TargetPath = Join-Path $root ('scripts\' + $p[1])
    $lnk.WorkingDirectory = $root
    $lnk.Save()
    Write-Host "ショートカット作成: $($p[0])"
}

# 毎日 12:00 にアップロード（PC がその時刻にオフなら、次に起動したときに実行）
$action = New-ScheduledTaskAction -Execute (Join-Path $root 'scripts\upload_daily.bat') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 12:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName 'wot-replay2video upload' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Shorts のバックログを予約投稿でアップロード' -Force | Out-Null
Write-Host "タスク登録: wot-replay2video upload（毎日 12:00）"
