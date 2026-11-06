# StatusCast ESP32 -- Windows-side control CLI for the board's /control/ API.
#
# The board authenticates every /control/* call with X-StatusCast-Token. The token
# lives in %APPDATA%\StatusCast\control-token; pass -Token once to store it.
#
#   .\sc-control.ps1 -Action status
#   .\sc-control.ps1 -Action scan
#   .\sc-control.ps1 -Action led -Led green
#   .\sc-control.ps1 -Action config -ConfigFile .\config.json -Yes
#   .\sc-control.ps1 -Action coredump -OutFile .\dump.bin
#   .\sc-control.ps1 -Action reboot -Yes
#
# Dangerous actions (reboot, config, coredump-clear) ask for confirmation unless
# -Yes is given. -Json prints the raw body for scripting.
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'scan', 'audit', 'log', 'coredump', 'coredump-clear', 'reboot',
                 'config', 'led', 'token')]
    [string]$Action = 'status',

    [string]$Base = 'http://statuscast.local:5099',
    [string]$Token = '',
    [string]$NewToken = '',
    [string]$ConfigFile = '',
    [string]$Led = '',
    [string]$OutFile = '',
    [int]$TimeoutSec = 5,
    [switch]$Json,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

function Get-TokenPath {
    $dir = Join-Path $env:APPDATA 'StatusCast'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    return (Join-Path $dir 'control-token')
}

function Get-ControlToken {
    if ($Token) {
        Set-Content -LiteralPath (Get-TokenPath) -Value $Token -NoNewline -Encoding ascii
        return $Token
    }
    $path = Get-TokenPath
    if (-not (Test-Path $path)) {
        throw "no control token stored; run: .\sc-control.ps1 -Action status -Token <token>"
    }
    return (Get-Content -LiteralPath $path -Raw).Trim()
}

function Confirm-Action([string]$what) {
    if ($Yes) { return }
    $answer = Read-Host "$what -- type 'yes' to continue"
    if ($answer -ne 'yes') { throw 'cancelled' }
}

function Invoke-Control {
    param(
        [string]$Method,
        [string]$Path,
        [string]$Body = '',
        [string]$ContentType = 'application/json',
        [int]$Attempts = 2
    )
    $uri = "$Base$Path"
    $headers = @{ 'X-StatusCast-Token' = $script:token }
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $params = @{
                Uri         = $uri
                Method      = $Method
                Headers     = $headers
                TimeoutSec  = $TimeoutSec
                ErrorAction = 'Stop'
            }
            if ($Body) {
                $params['Body'] = [System.Text.Encoding]::UTF8.GetBytes($Body)
                $params['ContentType'] = $ContentType
            }
            return Invoke-WebRequest @params
        } catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -eq 401) {
                throw 'board rejected the token (401); update it with -Token <token>'
            }
            if ($attempt -eq $Attempts) { throw }
            Start-Sleep -Milliseconds 400
        }
    }
}

function Show-Response($response) {
    $text = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
    if ($Json) {
        Write-Output $text
        return
    }
    try {
        $data = $text | ConvertFrom-Json
        Write-Output ($data | ConvertTo-Json -Depth 6)
    } catch {
        Write-Output $text
    }
}

$script:token = Get-ControlToken

switch ($Action) {
    'status' { Show-Response (Invoke-Control -Method GET -Path '/control/status') }
    'scan' {
        Write-Host 'scanning (BLE takes ~4 s) ...' -ForegroundColor Cyan
        Show-Response (Invoke-Control -Method GET -Path '/control/scan' -TimeoutSec 15)
    }
    'audit' { Show-Response (Invoke-Control -Method GET -Path '/control/audit') }
    'log' {
        $response = Invoke-Control -Method GET -Path '/control/log'
        $text = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
        Write-Output $text
    }
    'coredump' {
        if (-not $OutFile) { $OutFile = Join-Path (Get-Location) 'coredump.bin' }
        $response = Invoke-Control -Method GET -Path '/control/coredump' -TimeoutSec 20
        [System.IO.File]::WriteAllBytes($OutFile, $response.RawContentStream.ToArray())
        $expected = $response.Headers['X-Dump-SHA256']
        $actual = (Get-FileHash -LiteralPath $OutFile -Algorithm SHA256).Hash.ToLower()
        Write-Host "saved $OutFile ($($response.RawContentStream.Length) bytes)" -ForegroundColor Green
        Write-Host "sha256 expected=$expected actual=$actual"
        if ($expected -and ($actual -ne $expected)) { throw 'coredump checksum mismatch' }
    }
    'coredump-clear' {
        Confirm-Action 'Erase the coredump partition?'
        Show-Response (Invoke-Control -Method DELETE -Path '/control/coredump')
    }
    'reboot' {
        Confirm-Action 'Reboot the board?'
        Show-Response (Invoke-Control -Method POST -Path '/control/reboot' -Attempts 1)
        Write-Host 'board reboots in ~2 s; it will reconnect to WiFi and publish again' -ForegroundColor Yellow
    }
    'config' {
        if (-not $ConfigFile) { throw 'config requires -ConfigFile <path>' }
        if (-not (Test-Path $ConfigFile)) { throw "config file not found: $ConfigFile" }
        $body = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8
        # Validate locally before touching the board.
        try { $null = $body | ConvertFrom-Json } catch { throw "config file is not valid JSON: $_" }
        Confirm-Action "Replace the board's config.json with $ConfigFile?"
        Show-Response (Invoke-Control -Method POST -Path '/control/config' -Body $body)
        Write-Host 'the board rolls back automatically if no snapshot is published within 90 s' -ForegroundColor Yellow
    }
    'led' {
        if (-not $Led) { throw 'led requires -Led green|amber|red|off|auto' }
        if ($Led -eq 'auto') {
            Show-Response (Invoke-Control -Method POST -Path '/control/led' -Body '{"auto":true}')
        } else {
            Show-Response (Invoke-Control -Method POST -Path '/control/led' -Body ("{`"color`":`"$Led`"}"))
        }
    }
    'token' {
        if (-not $NewToken) { throw 'token requires -NewToken <value> (4..63 chars)' }
        if ($NewToken.Length -lt 4 -or $NewToken.Length -gt 63) { throw 'token must be 4..63 chars' }
        Confirm-Action 'Rotate the board control token?'
        Show-Response (Invoke-Control -Method POST -Path '/control/token' -Body ("{`"token`":`"$NewToken`"}"))
        Set-Content -LiteralPath (Get-TokenPath) -Value $NewToken -NoNewline -Encoding ascii
        Write-Host 'local token file updated' -ForegroundColor Green
    }
}
