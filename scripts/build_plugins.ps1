param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$project = Join-Path $root "plugins\ClockPlugin\ClockPlugin.csproj"
$output = Join-Path $root "plugins\ClockPlugin\bin\$Configuration\net10.0\ClockPlugin.dll"
$target = Join-Path $root "plugins\ClockPlugin.dll"

dotnet build $project -c $Configuration
if ($LASTEXITCODE -ne 0) {
    throw "Plugin build failed."
}

Copy-Item -LiteralPath $output -Destination $target -Force
Write-Host "Installed: $target"
