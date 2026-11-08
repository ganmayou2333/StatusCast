# StatusCast ESP32 -- self-check for the serial port resolver.
#
#   .\tools\test-ports.ps1
#
# Covers the six decisions ports.ps1 makes, without needing a board attached:
# the candidate list is injected instead of enumerated. Exits non-zero on the
# first mismatch so it can gate a change to the resolver.
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ports.ps1')

$script:checks = 0
$script:failures = 0

function Check {
    param([string]$Name, [scriptblock]$Body)
    $script:checks += 1
    try {
        $problem = & $Body
        if ($problem) {
            $script:failures += 1
            Write-Output ("FAIL  {0}`n      {1}" -f $Name, $problem)
        } else {
            Write-Output ("PASS  {0}" -f $Name)
        }
    } catch {
        $script:failures += 1
        Write-Output ("FAIL  {0}`n      unexpected exception: {1}" -f $Name, $_.Exception.Message)
    }
}

function NewCandidate {
    # $Pid is a read-only automatic variable, hence ProductId.
    param([string]$Port, [string]$Vid, [string]$ProductId, [string]$Name = 'serial device')
    [pscustomobject]@{
        Port     = $Port
        Name     = $Name
        DeviceId = "USB\VID_$Vid&PID_$ProductId&MI_00\7&0&0000"
    }
}

$board    = NewCandidate -Port 'COM4' -Vid '303A' -ProductId '1001' -Name 'USB JTAG/serial debug unit'
$bridge   = NewCandidate -Port 'COM7' -Vid '1A86' -ProductId '55D3' -Name 'USB-SERIAL CH343'
$other    = NewCandidate -Port 'COM9' -Vid '0403' -ProductId '6001' -Name 'FT232R USB UART'

# --- 1. exactly one board, even alongside an unrelated port -------------------
Check 'auto picks the board when an unrelated port is present' {
    $got = Resolve-ScSerialPort -Port 'auto' -Tool 't' -Candidates @($other, $board) -WarningAction SilentlyContinue 6>$null
    if ($got -ne 'COM4') { "resolved '$got', expected COM4" }
}

# --- 2. the CH343 bridge counts too ----------------------------------------
Check 'auto accepts the CH343 bridge' {
    $got = Resolve-ScSerialPort -Port 'auto' -Tool 't' -Candidates @($bridge) -WarningAction SilentlyContinue 6>$null
    if ($got -ne 'COM7') { "resolved '$got', expected COM7" }
}

# --- 3. no board ------------------------------------------------------------
Check 'auto refuses when only unrelated ports exist' {
    try {
        $null = Resolve-ScSerialPort -Port 'auto' -Tool 't' -Candidates @($other) -WarningAction SilentlyContinue 6>$null
        'did not throw'
    } catch {
        if ($_.Exception.Message -notmatch 'is attached') { "wrong message: $($_.Exception.Message)" }
    }
}

# --- 4. two boards: refusing beats guessing ---------------------------------
Check 'auto refuses when two boards are attached' {
    $second = NewCandidate -Port 'COM8' -Vid '1A86' -ProductId '55D3' -Name 'second board'
    try {
        $null = Resolve-ScSerialPort -Port 'auto' -Tool 't' -Candidates @($board, $second) -WarningAction SilentlyContinue 6>$null
        'did not throw'
    } catch {
        if ($_.Exception.Message -notmatch 'more than one') { "wrong message: $($_.Exception.Message)" }
    }
}

# --- 5. explicit port that does not exist ----------------------------------
Check 'explicit missing port is rejected' {
    try {
        $null = Resolve-ScSerialPort -Port 'COM42' -Tool 't' -Candidates @($board) -WarningAction SilentlyContinue 6>$null
        'did not throw'
    } catch {
        if ($_.Exception.Message -notmatch 'does not exist') { "wrong message: $($_.Exception.Message)" }
    }
}

# --- 6. explicit port that exists but is somebody else's --------------------
Check 'explicit foreign port resolves but warns about DTR/RTS' {
    $warnings = @()
    $got = Resolve-ScSerialPort -Port 'COM9' -Tool 't' -Candidates @($other) `
        -WarningVariable warnings -WarningAction SilentlyContinue 6>$null
    if ($got -ne 'COM9') { "resolved '$got', expected COM9" }
    elseif ($warnings.Count -eq 0) { 'no warning was emitted' }
    elseif (($warnings -join ' ') -notmatch 'DTR') { "warning did not mention DTR/RTS: $($warnings -join ' ')" }
}

# --- 7. explicit board port is accepted quietly ----------------------------
Check 'explicit board port resolves' {
    $got = Resolve-ScSerialPort -Port 'com4' -Tool 't' -Candidates @($board) -WarningAction SilentlyContinue 6>$null
    if ($got -ne 'COM4') { "resolved '$got', expected COM4 (normalised from com4)" }
}

# --- 8. junk ---------------------------------------------------------------
Check 'non-COM value is rejected' {
    try {
        $null = Resolve-ScSerialPort -Port 'ttyUSB0' -Tool 't' -Candidates @($board) -WarningAction SilentlyContinue 6>$null
        'did not throw'
    } catch {
        if ($_.Exception.Message -notmatch "must be 'auto'") { "wrong message: $($_.Exception.Message)" }
    }
}

Write-Output ""
Write-Output ("{0} checks, {1} failures" -f $script:checks, $script:failures)
if ($script:failures -ne 0) { exit 1 }
