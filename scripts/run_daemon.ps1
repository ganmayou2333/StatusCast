#requires -Version 5.1
<#
.SYNOPSIS
    Start the resident StatusCast daemon.

.DESCRIPTION
    Runs core.daemon in the foreground so a scheduler, terminal, or the display
    target can own the process lifetime. Use -Once for a single collection and
    snapshot write, or -Stop to ask a running daemon to shut down.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_daemon.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_daemon.ps1 -Once

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_daemon.ps1 -Stop
#>
[CmdletBinding()]
param(
    [string]$Config,
    [switch]$Once,
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    [Console]::Error.WriteLine("Python venv not found: $PythonExe")
    [Console]::Error.WriteLine("Run: py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt")
    exit 3
}

if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $ProjectRoot 'config.toml'
}

Set-Location -LiteralPath $ProjectRoot

$Arguments = @('-m', 'core.daemon', '--config', $Config)
if ($Once) {
    $Arguments += '--once'
}
if ($Stop) {
    $Arguments += '--stop'
}

& $PythonExe @Arguments
exit $LASTEXITCODE
