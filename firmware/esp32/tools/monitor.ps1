# StatusCast ESP32 -- serial monitor helper (run from PowerShell).
#
# -Port auto (the default) picks the single CH343/CH340 on the machine; see
# ports.ps1. Attaching the monitor resets the board, so pointing it at a stale
# port number resets something else.
param(
    [string]$Port = 'auto',
    [string[]]$BoardIds = @()
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ports.ps1')

$bat = Join-Path $PSScriptRoot 'build.bat'
if (-not (Test-Path $bat)) { throw "build.bat not found next to this script" }

$resolveArgs = @{ Port = $Port; Tool = 'monitor.ps1' }
if ($BoardIds.Count -gt 0) { $resolveArgs['BoardIds'] = $BoardIds }
$resolved = Resolve-ScSerialPort @resolveArgs

Write-Host "monitoring $resolved (Ctrl+] to exit) ..." -ForegroundColor Cyan
& cmd.exe /c "`"$bat`" -p $resolved monitor"
