$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$taskTemp = Join-Path $projectRoot 'tmp'
New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
$env:TMP = $taskTemp
$env:TEMP = $taskTemp
$env:PYTHONUTF8 = '1'

function Invoke-FrontendTool {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ToolArgs)
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        $taskPnpm = Get-Command pnpm.cmd,pnpm -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($taskPnpm) {
            & $taskPnpm.Source @ToolArgs
        } else {
            $taskNode = (Get-Command node -ErrorAction Stop).Source
            $bundledPnpm = Join-Path (Split-Path (Split-Path $taskNode -Parent) -Parent) 'node_modules\pnpm\bin\pnpm.mjs'
            if (-not (Test-Path -LiteralPath $bundledPnpm)) {
                throw '找不到 pnpm。請提供已安裝的 pnpm 11，或使用含 pnpm 的可用開發工具鏈。此腳本不做系統級安裝。'
            }
            & $taskNode $bundledPnpm @ToolArgs
        }
        if ($LASTEXITCODE -ne 0) { throw "前端工具執行失敗，退出碼 $LASTEXITCODE" }
    } finally { Pop-Location }
}
