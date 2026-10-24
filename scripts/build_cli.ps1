param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not $Python) {
    $Python = Join-Path $root ".venv\Scripts\python.exe"
}

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}

& $Python -m pip install --disable-pip-version-check -r (Join-Path $root "requirements-build.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install build dependencies."
}

Push-Location $root
try {
    & $Python -m PyInstaller --noconfirm --clean statuscast-cli.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Built: $root\dist\statuscast-cli.exe"
