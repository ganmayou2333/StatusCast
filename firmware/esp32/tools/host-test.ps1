# StatusCast ESP32 -- run the host-side contract tests (no board required).
#
# The same core/*.c files are compiled here with a plain C compiler and checked
# against schema/status-contract.json. A Zig toolchain is used because it is a
# single self-contained download; any C11 compiler works.
param(
    [string]$Zig = ''
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$root = Split-Path -Parent $here
$core = Join-Path $root 'main\core'
$cjson = Join-Path $core 'vendor\cjson'
$contract = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $root))) 'schema\status-contract.json'
$out = Join-Path $root 'host_test\build'
New-Item -ItemType Directory -Force -Path $out | Out-Null

if (-not $Zig) {
    $candidates = @(
        'G:\tools\zig\zig-windows-x86_64-0.13.0\zig.exe',
        (Join-Path $env:LOCALAPPDATA 'zig\zig.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $Zig = $candidate; break }
    }
}
if (-not $Zig) {
    $onPath = Get-Command zig -ErrorAction SilentlyContinue
    if ($onPath) { $Zig = $onPath.Source }
}
if (-not $Zig) {
    throw "no Zig compiler found; pass -Zig <path to zig.exe> (see firmware/esp32/README.md)"
}

$exe = Join-Path $out 'sc_host_test.exe'
$sources = @(
    (Join-Path $root 'host_test\test_main.c'),
    (Join-Path $core 'sc_core.c'),
    (Join-Path $core 'sc_config.c'),
    (Join-Path $core 'sc_json.c'),
    (Join-Path $core 'sc_snapshot.c'),
    (Join-Path $core 'sc_wire_parse.c'),
    (Join-Path $core 'sc_display.c'),
    (Join-Path $cjson 'cJSON.c')
)
$define = '-DSC_CONTRACT_PATH=' + [char]34 + $contract.Replace('\', '/') + [char]34

Write-Host "compiling host tests with $Zig ..." -ForegroundColor Cyan
& $Zig cc -std=c11 -Wall -Wextra -Wno-unused-parameter `
    -I $core -I $cjson $define @sources -o $exe
if ($LASTEXITCODE -ne 0) { throw "host test build failed" }

& $exe
if ($LASTEXITCODE -ne 0) { throw "host tests failed" }
