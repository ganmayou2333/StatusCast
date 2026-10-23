try {
    $status = Invoke-RestMethod -Uri 'http://localhost:5099/status/' -UseBasicParsing -TimeoutSec 3
    Write-Output $status.text
} catch {
    Write-Output "🔋 -- | 🎵 --"
}