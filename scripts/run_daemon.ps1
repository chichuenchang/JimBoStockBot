# Starts the live daemon in its own console window; refuses if one already runs (two daemons lock the DB).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='stockbot.exe'" |
    Where-Object { $_.CommandLine -match 'stockbot(\.exe)?"?\s+run\b' }
if ($running) {
    Write-Host "Daemon already running (pid $($running.ProcessId -join ', ')). Not starting another."
    exit 1
}

Start-Process powershell -WorkingDirectory $repo -ArgumentList '-NoExit', '-Command',
    "`$Host.UI.RawUI.WindowTitle = 'StockBot daemon'; uv run stockbot run"
Write-Host "Started in window 'StockBot daemon'. Dashboard: http://127.0.0.1:8000"
