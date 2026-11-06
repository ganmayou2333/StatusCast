# StatusCast ESP32 -- serial monitor helper (run from PowerShell).
param([string]$Port = 'COM3')

$ErrorActionPreference = 'Stop'
$bat = Join-Path $PSScriptRoot 'build.bat'
if (-not (Test-Path $bat)) { throw "build.bat not found next to this script" }

Write-Host "monitoring $Port (Ctrl+] to exit) ..." -ForegroundColor Cyan
& cmd.exe /c "`"$bat`" -p $Port monitor"
