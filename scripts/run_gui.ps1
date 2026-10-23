#requires -Version 5.1
<#
.SYNOPSIS
    Build and launch the StatusCast WinUI 3 desktop app.

.DESCRIPTION
    Requires the .NET 10 SDK (or a compatible .NET SDK with WinUI 3 tooling)
    and Windows App SDK packages restored by NuGet.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_gui.ps1
#>
[CmdletBinding()]
param(
    [string]$Configuration = 'Debug',
    [string]$Platform = 'x64'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectFile = Join-Path $ProjectRoot 'gui\StatusCast.Gui\StatusCast.Gui.csproj'

if (-not (Test-Path -LiteralPath $ProjectFile -PathType Leaf)) {
    [Console]::Error.WriteLine("WinUI project not found: $ProjectFile")
    exit 3
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine('dotnet SDK not found. Install the .NET 10 SDK.')
    exit 3
}

Push-Location -LiteralPath $ProjectRoot
try {
    & dotnet run --project $ProjectFile -c $Configuration "-p:Platform=$Platform"
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
