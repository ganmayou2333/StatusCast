# StatusCast ESP32 -- send console commands over the UART (run from PowerShell).
#
# Provisioning is two steps: WiFi credentials go to NVS, the device list goes to
# LittleFS. Both are plain console commands, so this script just feeds lines to
# the port. Opening the port asserts DTR/RTS and resets the board, so it waits for
# the boot banner before writing anything.
#
#   .\tools\serial-command.ps1 -Port COM3 -Lines 'net ssid <your-ssid>'
#   .\tools\serial-command.ps1 -Port COM3 -LinesFile .\my-config-lines.txt
#
# A config paste ends like this (one command per line):
#   cfg put
#   {"RefreshIntervalSeconds":30,...}
#   EOF
param(
    [string]$Port = 'COM3',
    [int]$Baud = 115200,
    [int]$BootWaitMs = 3500,
    [int]$CaptureSeconds = 8,
    [string[]]$Lines = @(),
    [string]$LinesFile = ''
)

$ErrorActionPreference = 'Stop'
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
    throw "cannot open $Port : $_"
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
