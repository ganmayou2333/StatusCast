# StatusCast ESP32 -- flash helper (run from PowerShell).
#
# Requires ESP-IDF 5.5.6 at E:\Espressif (see firmware/esp32/README.md) and the
# CH343 UART on the given COM port.
param(
    [string]$Port = 'COM3',
    [int]$Baud = 460800,
    [switch]$Monitor
)

$ErrorActionPreference = 'Stop'
$bat = Join-Path $PSScriptRoot 'build.bat'
if (-not (Test-Path $bat)) { throw "build.bat not found next to this script" }

Write-Host "flashing via $Port @ $Baud ..." -ForegroundColor Cyan
& cmd.exe /c "`"$bat`" -p $Port -b $Baud flash"
if ($LASTEXITCODE -ne 0) { throw "flash failed with exit code $LASTEXITCODE" }

if ($Monitor) {
    Write-Host "opening monitor (Ctrl+] to exit) ..." -ForegroundColor Cyan
    & cmd.exe /c "`"$bat`" -p $Port monitor"
}
