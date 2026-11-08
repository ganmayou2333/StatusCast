# StatusCast ESP32 -- send console commands over the UART (run from PowerShell).
#
# Provisioning is two steps: WiFi credentials go to NVS, the device list goes to
# LittleFS. Both are plain console commands, so this script just feeds lines to
# the port. Opening the port asserts DTR/RTS and resets the board, so it waits for
# the boot banner before writing anything.
#
#   .\tools\serial-command.ps1 -Lines 'net ssid <your-ssid>'
#   .\tools\serial-command.ps1 -LinesFile .\my-config-lines.txt
#
# -Port defaults to 'auto' and picks the single CH343/CH340 on the machine: the
# number changes after a replug, and opening the wrong port resets the wrong
# board. See ports.ps1.
#
# A config paste ends like this (one command per line):
#   cfg put
#   {"RefreshIntervalSeconds":30,...}
#   EOF
param(
    [string]$Port = 'auto',
    [int]$Baud = 115200,
    [int]$BootWaitMs = 3500,
    [int]$CaptureSeconds = 8,
    [string[]]$Lines = @(),
    [string]$LinesFile = '',
    [string[]]$BoardIds = @()
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ports.ps1')

$resolveArgs = @{ Port = $Port; Tool = 'serial-command.ps1' }
if ($BoardIds.Count -gt 0) { $resolveArgs['BoardIds'] = $BoardIds }
# Resolve before opening: this script asserts DTR/RTS, so a stale port number
# would reset an unrelated device instead of failing harmlessly.
$Port = Resolve-ScSerialPort @resolveArgs

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, 'None', 8, 'One'
$sp.ReadTimeout = 300
# Without a bounded write timeout a wedged USB-serial TX blocks forever and the
# whole script hangs instead of reporting the problem.
$sp.WriteTimeout = 1500
$sp.Encoding = [System.Text.Encoding]::UTF8
$sp.DtrEnable = $false
$sp.RtsEnable = $false
try {
    $sp.Open()
} catch {
    throw ("cannot open $Port : $($_).`n" +
        "If this port came from -Port auto, the board may have re-enumerated between " +
        "discovery and open; re-run the command. Attaching this script asserts DTR/RTS " +
        "and resets the target, so do not point it at a port you do not recognise.")
}
$sp.DtrEnable = $false
$sp.RtsEnable = $false
Start-Sleep -Milliseconds $BootWaitMs

$queue = @()
if ($LinesFile) { $queue += Get-Content -LiteralPath $LinesFile -Encoding UTF8 }
$queue += $Lines
try {
    foreach ($line in $queue) {
        $sp.Write("$line`r`n")
        Start-Sleep -Milliseconds 250
    }
} catch {
    $sp.Close()
    throw "serial write failed (is the port wedged? unplug/replug the USB cable): $_"
}

$sw = [Diagnostics.Stopwatch]::StartNew()
$out = New-Object System.Text.StringBuilder
while ($sw.Elapsed.TotalSeconds -lt $CaptureSeconds) {
    try { $chunk = $sp.ReadExisting(); if ($chunk) { [void]$out.Append($chunk) } } catch { }
    Start-Sleep -Milliseconds 100
}
$sp.Close()
Write-Output $out.ToString()
