#requires -Version 5.1
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$PythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    $PythonExe = 'python'
}
$Script = Join-Path $PSScriptRoot 'status_core.py'

try {
    $output = & $PythonExe $Script 2>$null
    $exitCode = $LASTEXITCODE
}
catch {
    $exitCode = 1
}

if ($exitCode -ne 0) {
    Write-Output '📱 设备连接失败'
    exit 0
}

$text = ($output -join "`n").Trim()
if ($text -notmatch '^STATUS:BATTERY:([^|]+)\|CHARGING:([^|]+)\|MEDIA:([^|]*)\|([^|]*)\|([^|]*)\|MODEL:([^|]*)$') {
    Write-Output '📱 设备未连接'
    exit 0
}

$battery = $Matches[1].Trim()
$charging = $Matches[2].Trim().ToLower()
$title = $Matches[3].Trim()
$artist = $Matches[4].Trim()
$model = $Matches[6].Trim()

if ($battery -match '^\d+$') {
    $batteryText = "🔋 $battery%"
    if ($charging -eq 'true') {
        $batteryText += ' ⚡'
    }
}
else {
    $batteryText = '🔋 未知'
}

if ($title -and $artist -and $title -ne 'NA' -and $artist -ne 'NA') {
    $mediaText = "🎵 $title - $artist"
}
else {
    $mediaText = '🎵 暂无播放'
}

$modelMap = @{
    'iPhone15,3' = '14 Pro Max'
    'iPhone14,3' = '13 Pro Max'
}
$modelText = ''
if ($model -and $model -ne 'NA') {
    if ($modelMap.ContainsKey($model)) {
        $modelText = $modelMap[$model]
    }
    else {
        $modelText = $model
    }
}

if ($modelText) {
    Write-Output "📱 $modelText | $batteryText | $mediaText"
}
else {
    Write-Output "$batteryText | $mediaText"
}
exit 0
