param(
    [string]$Configuration = 'Debug',
    [switch]$Hidden
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$project = Join-Path $root 'src\StatusCast.Daemon\StatusCast.Daemon.csproj'
$exe = Join-Path $root "src\StatusCast.Daemon\bin\$Configuration\net10.0\StatusCast.Daemon.exe"

if (-not (Test-Path -LiteralPath $project)) {
    throw "Daemon project not found: $project"
}

if (-not (Test-Path -LiteralPath $exe)) {
    dotnet build $project -c $Configuration
    if ($LASTEXITCODE -ne 0) {
        throw 'Daemon build failed.'
    }
}

if ($Hidden) {
    Start-Process -FilePath $exe -WorkingDirectory $root -WindowStyle Hidden | Out-Null
    Write-Host "StatusCast.Daemon started hidden."
    return
}

Set-Location -LiteralPath $root
& $exe
exit $LASTEXITCODE
