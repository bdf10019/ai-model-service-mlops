param([switch]$RealModel)
. "$PSScriptRoot\common.ps1"
Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Force -Path 'reports' | Out-Null
    & $pythonPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw '依賴一致性失敗。' }
    & $pythonPath -m ruff check backend tests scripts
    if ($LASTEXITCODE -ne 0) { throw '程式檢查失敗。' }
    & $pythonPath -m pytest --junitxml=reports/backend-tests.xml
    if ($LASTEXITCODE -ne 0) { throw '後端測試失敗。' }
    Invoke-FrontendTool build
    Invoke-FrontendTool exec vitest run --maxWorkers=1 --reporter=json --outputFile=../reports/frontend-tests.json
    $env:CONSOLE_REAL_MODEL = if ($RealModel) {'1'} else {'0'}
    $env:CONSOLE_STATE_DIR = 'tmp/e2e-' + [guid]::NewGuid().ToString('N')
    Invoke-FrontendTool test:e2e
    & $pythonPath scripts/privacy_check.py
    if ($LASTEXITCODE -ne 0) { throw '隱私檢查失敗。' }
    & $pythonPath scripts/evidence_index.py
    if ($LASTEXITCODE -ne 0) { throw '證據索引失敗。' }
} finally {
    Remove-Item Env:CONSOLE_REAL_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:CONSOLE_STATE_DIR -ErrorAction SilentlyContinue
    Pop-Location
}
