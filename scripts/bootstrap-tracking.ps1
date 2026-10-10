. "$PSScriptRoot\common.ps1"
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv-mlflow\Scripts\python.exe')) {
        & $pythonPath -m venv .venv-mlflow
        if ($LASTEXITCODE -ne 0) { throw '作品內可選追蹤環境建立失敗。' }
    }
    & .\.venv-mlflow\Scripts\python.exe -m pip install -r requirements.mlflow.lock.txt
    if ($LASTEXITCODE -ne 0) { throw '可選追蹤鎖版依賴安裝失敗。' }
    & .\.venv-mlflow\Scripts\python.exe -m pip check
    if ($LASTEXITCODE -ne 0) { throw '可選追蹤依賴檢查失敗。' }
} finally { Pop-Location }
