# StatusCast ESP32 -- flash helper (run from PowerShell).
#
# Requires ESP-IDF 5.5.6 at E:\Espressif (see firmware/esp32/README.md).
#
# The port is resolved, not guessed: -Port auto (the default) picks the single
# CH343/CH340 on the machine, because the port number changes after a replug.
# See ports.ps1 for why that matters.
param(
    [string]$Port = 'auto',
    [int]$Baud = 460800,
    [switch]$Monitor,
    # Override the accepted USB-UART ids, e.g. -BoardIds '1A86:7523'.
    [string[]]$BoardIds = @()
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ports.ps1')

$bat = Join-Path $PSScriptRoot 'build.bat'
if (-not (Test-Path $bat)) { throw "build.bat not found next to this script" }

$resolveArgs = @{ Port = $Port; Tool = 'flash.ps1' }
if ($BoardIds.Count -gt 0) { $resolveArgs['BoardIds'] = $BoardIds }
$resolved = Resolve-ScSerialPort @resolveArgs

Write-Host "flashing via $resolved @ $Baud ..." -ForegroundColor Cyan
& cmd.exe /c "`"$bat`" -p $resolved -b $Baud flash"
if ($LASTEXITCODE -ne 0) { throw "flash failed with exit code $LASTEXITCODE" }

if ($Monitor) {
    Write-Host "opening monitor (Ctrl+] to exit) ..." -ForegroundColor Cyan
    & cmd.exe /c "`"$bat`" -p $resolved monitor"
}
