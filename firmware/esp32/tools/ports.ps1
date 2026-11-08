# StatusCast ESP32 -- serial port resolution shared by flash/monitor/serial-command.
#
# Why this exists: every tool used to default to `-Port COM3`, and the CH343 on
# this board comes back as a different number after a replug (COM3 -> COM4 was
# observed on the reference unit). Two things can then go wrong, and the second
# one is not just an error message:
#
#   * the command just fails, or
#   * it succeeds against the WRONG port. serial-command.ps1 asserts DTR/RTS on
#     open by design, so pointing it at a typo'd port resets whatever device is
#     actually there.
#
# So a port is resolved, not guessed:
#
#   -Port auto      pick the single CH343/CH340 on the machine (default)
#   -Port COM4      explicit; validated to exist, and to look like the board
#
# Dot-source this file and call Resolve-ScSerialPort. Do not add a port default
# back into the individual scripts.

# Which USB devices count as "the board". Either 'VID:PID' (both must match) or a
# bare 'VID' (any product from that vendor).
#
#   303A        Espressif Systems -- the ESP32-S3's own USB-Serial-JTAG. This is
#               what the reference board actually enumerates as (303A:1001,
#               "USB JTAG/serial debug unit" + a CDC port); it was NOT the CH343
#               the README used to describe, which is the other USB socket on the
#               same board and only appears when that socket is the one plugged in.
#   1A86:55D3   CH343  -- the UART bridge on that second socket
#   1A86:7523   CH340  -- same vendor, common on other ESP32 dev boards
#
# Getting this list wrong is not cosmetic: it decides whether -Port auto finds
# the board at all.
$script:ScDefaultBoardIds = @('303A', '1A86:55D3', '1A86:7523')

function Get-ScSerialCandidate {
    <#
      Every serial port the machine reports, with the PnP id so the caller can
      tell the board from an unrelated device. Returns @() when enumeration is
      unavailable rather than throwing -- a missing CIM provider should not stop
      an explicit -Port COM4 from working.
    #>
    $candidates = @()
    try {
        $devices = @(Get-CimInstance -ClassName Win32_PnPEntity -ErrorAction Stop)
    } catch {
        try {
            $devices = @(Get-WmiObject -Class Win32_PnPEntity -ErrorAction Stop)
        } catch {
            return @()
        }
    }
    foreach ($device in $devices) {
        $name = [string]$device.Name
        if (-not $name) { continue }
        $match = [regex]::Match($name, '\((COM\d+)\)')
        if (-not $match.Success) { continue }
        $candidates += [pscustomobject]@{
            Port     = $match.Groups[1].Value
            Name     = ($name -replace '\s*\(COM\d+\)\s*', '').Trim()
            DeviceId = [string]$device.PNPDeviceID
        }
    }
    return $candidates
}

function Test-ScBoardCandidate {
    param(
        [Parameter(Mandatory = $true)] $Candidate,
        [string[]]$BoardIds = $script:ScDefaultBoardIds
    )
    if (-not $Candidate.DeviceId) { return $false }
    $deviceId = $Candidate.DeviceId.ToUpperInvariant()
    foreach ($id in $BoardIds) {
        if ($id -match ':') {
            # VID:PID -- both halves must appear, in that order.
            $want = ($id -replace ':', '&PID_').ToUpperInvariant()
            if ($deviceId.Contains($want)) { return $true }
        } else {
            if ($deviceId.Contains("VID_$($id.ToUpperInvariant())")) { return $true }
        }
    }
    return $false
}

function Format-ScCandidateList {
    param([object[]]$Candidates = @(), [string[]]$BoardIds = $script:ScDefaultBoardIds)
    if ($Candidates.Count -eq 0) {
        return "  (no serial ports were found at all)"
    }
    $lines = @()
    foreach ($candidate in ($Candidates | Sort-Object Port)) {
        $mark = if (Test-ScBoardCandidate -Candidate $candidate -BoardIds $BoardIds) { 'board' } else { '     ' }
        $lines += ("  {0}  [{1}]  {2}" -f $candidate.Port, $mark, $candidate.Name)
    }
    return ($lines -join [Environment]::NewLine)
}

function Resolve-ScSerialPort {
    <#
      Returns a COM port name to use, or throws with an actionable message.

      -Port 'auto' (or empty) requires exactly one board-like device.
      -Port 'COM4' is accepted when that port exists; a port that does not look
      like the board is allowed but warned about, because the tools assert
      DTR/RTS and reset whatever they open.
    #>
    [CmdletBinding()]
    param(
        [string]$Port = 'auto',
        [string]$Tool = 'this script',
        [string[]]$BoardIds = $script:ScDefaultBoardIds,
        # Injectable for tests: lets the resolution rules be exercised without
        # the hardware they describe. Null means "enumerate this machine".
        [object[]]$Candidates = $null
    )

    $expected = ($BoardIds -join ', ')
    $candidates = if ($null -ne $Candidates) { @($Candidates) } else { @(Get-ScSerialCandidate) }

    $auto = [string]::IsNullOrWhiteSpace($Port) -or ($Port -ieq 'auto')
    if ($auto) {
        $boards = @($candidates | Where-Object { Test-ScBoardCandidate -Candidate $_ -BoardIds $BoardIds })
        if ($boards.Count -eq 1) {
            Write-Host "$Tool`: using $($boards[0].Port) ($($boards[0].Name))" -ForegroundColor Cyan
            return $boards[0].Port
        }
        if ($boards.Count -gt 1) {
            throw @"
$Tool`: more than one board-like serial port is present, so -Port auto cannot choose.
Expected hardware: VID $expected.

$($boards | ForEach-Object { "  " + $_.Port + "  " + $_.Name } | Out-String)
Re-run with -Port <COMx> to pick one.
"@
        }
        throw @"
$Tool`: no Espressif board (VID $expected) is attached.

Ports this machine does report:
$(Format-ScCandidateList -Candidates $candidates -BoardIds $BoardIds)

Check the cable, then confirm the board appears in Device Manager under
"Ports (COM & LPT)". Pass -Port <COMx> to override the check.
"@
    }

    if ($Port -notmatch '^COM\d+$') {
        throw "$Tool`: -Port must be 'auto' or a COM port name such as COM4 (got '$Port')."
    }
    $wanted = $Port.ToUpperInvariant()

    $known = @($candidates | Where-Object { $_.Port.ToUpperInvariant() -eq $wanted })
    if ($known.Count -eq 0) {
        # Enumeration may be unavailable; fall back to asking .NET directly.
        $names = @()
        try { $names = @([System.IO.Ports.SerialPort]::GetPortNames()) } catch { }
        if ($names.Count -gt 0 -and ($names | Where-Object { $_ -ieq $wanted })) {
            Write-Warning "$Tool`: $wanted exists but its USB id could not be read; opening it anyway."
            return $wanted
        }
        throw @"
$Tool`: $wanted does not exist on this machine.

Ports this machine does report:
$(Format-ScCandidateList -Candidates $candidates -BoardIds $BoardIds)

Port numbers change after a replug; use -Port auto to let this script find the
board instead of typing a number from memory.
"@
    }

    $candidate = $known[0]
    if (-not (Test-ScBoardCandidate -Candidate $candidate -BoardIds $BoardIds)) {
        Write-Warning ("$Tool`: $wanted is '$($candidate.Name)', not an Espressif board (VID $expected). " +
            "Opening it asserts DTR/RTS and will reset whatever is on that port.")
    } else {
        Write-Host "$Tool`: using $wanted ($($candidate.Name))" -ForegroundColor Cyan
    }
    return $wanted
}
