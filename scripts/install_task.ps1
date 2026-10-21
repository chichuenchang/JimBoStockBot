# Registers StockBot as a Windows scheduled task: starts at logon, restarts on failure.
# Run yourself in PowerShell from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing $python. Run 'uv sync' in $repo first." }

$action = New-ScheduledTaskAction -Execute $python -Argument "-m stockbot run" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "StockBot" -Action $action -Trigger $trigger -Settings $settings `
    -Description "LLM paper-trading arena daemon" -Force | Out-Null

Write-Host "Registered task 'StockBot'. Start now: Start-ScheduledTask -TaskName StockBot"
Write-Host "Dashboard: http://127.0.0.1:8000"
Write-Host "Reminder: the PC must stay awake 09:30-16:00 ET. Settings > System > Power > Sleep = Never (when plugged in)."
