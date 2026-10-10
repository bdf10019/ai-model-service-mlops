param([int]$Port=8120)
. "$PSScriptRoot\common.ps1"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '請先執行 scripts\bootstrap.ps1。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) { throw '前端尚未建置，請先執行 bootstrap。' }
Write-Host "本機控制台：http://127.0.0.1:$Port；使用 Ctrl+C 優雅停止。"
Push-Location $projectRoot
try {
    & $pythonPath -m uvicorn console_app.app:factory --factory --host 127.0.0.1 --port $Port --no-access-log
} finally { Pop-Location }
