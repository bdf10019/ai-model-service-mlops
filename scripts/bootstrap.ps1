. "$PSScriptRoot\common.ps1"
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 虛擬環境建立失敗。' }
    }
    & $pythonPath -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python 依賴安裝失敗。' }
    & $pythonPath -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw '產品套件安裝失敗。' }
    Invoke-FrontendTool install --frozen-lockfile
    Invoke-FrontendTool build
} finally { Pop-Location }
