param([Parameter(Mandatory=$true)][string]$SourceProject)
. "$PSScriptRoot\common.ps1"
$sourceResolved = (Resolve-Path -LiteralPath $SourceProject).Path
$modelSource = Join-Path $sourceResolved 'artifacts\gguf\modelforge-tw-q8_0.gguf'
$modelTarget = Join-Path $projectRoot 'artifacts\models\q8\model.gguf'
$engineTarget = Join-Path $projectRoot 'tools\llama'
if (Test-Path -LiteralPath $modelTarget) { throw '模型目標已存在；此腳本不覆寫既有產物。' }
if (Test-Path -LiteralPath $engineTarget) { throw '引擎目標已存在；此腳本不覆寫既有產物。' }
$expected = 'D4729EA11EA540D46BE45EFFE7B41FFBE55BC51CE7E42BF7443CD2BBF84CA9ED'
if ((Get-FileHash -LiteralPath $modelSource -Algorithm SHA256).Hash -ne $expected) { throw '來源模型 SHA-256 不符，未匯入。' }
New-Item -ItemType Directory -Force -Path (Split-Path $modelTarget -Parent),$engineTarget | Out-Null
Copy-Item -LiteralPath $modelSource -Destination $modelTarget
Copy-Item -Path (Join-Path $sourceResolved 'tools\llama.cpp-bin\*') -Destination $engineTarget
Push-Location $projectRoot
try {
    & $pythonPath -c "from pathlib import Path; from console_app.engine import verify_import; r=verify_import(Path.cwd()); print('匯入及完整雜湊驗證通過，檔案數：',r['file_count'])"
    if ($LASTEXITCODE -ne 0) { throw '匯入驗證失敗，保留失敗檔案供檢查，不標示成功。' }
} finally { Pop-Location }
