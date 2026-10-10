param([switch]$Build)
. "$PSScriptRoot\common.ps1"
Push-Location $projectRoot
try {
    $taskDocker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $taskDocker) { throw '尚無可用 Docker。本腳本不安裝或啟用系統工具；請先閱讀容器環境變更單。' }
    foreach ($taskImage in @($env:NODE_IMAGE,$env:PYTHON_IMAGE)) {
        if ($taskImage -notmatch '^docker\.io/library/(node|python)@sha256:[a-f0-9]{64}$') { throw '基底映像必須是已審核的官方 Node／Python 固定 digest；不得使用 mutable tag 或任意 registry。' }
    }
    & $taskDocker.Source info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon 不可用。' }
    & $taskDocker.Source compose version
    if ($LASTEXITCODE -ne 0) { throw 'Compose 不可用。' }
    & $taskDocker.Source compose -f infra/compose.fixture.yaml config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose 設定驗證失敗。' }
    New-Item -ItemType Directory -Force -Path 'state/container-console','tmp/container-console' | Out-Null
    if ($Build) {
        & $taskDocker.Source compose -f infra/compose.fixture.yaml build
        if ($LASTEXITCODE -ne 0) { throw '容器建置失敗。' }
    }
    & $taskDocker.Source compose -f infra/compose.fixture.yaml up -d --wait --no-build
    if ($LASTEXITCODE -ne 0) { throw '容器啟動／健康檢查失敗。' }
    $taskResponse=Invoke-WebRequest -Uri 'http://127.0.0.1:8128/api/setup' -TimeoutSec 5
    if ($taskResponse.StatusCode -ne 200) { throw '容器 HTTP 健康未通過。' }
    $taskDigest=& $taskDocker.Source inspect --format '{{.Image}}' ai-model-console-fixture-console-1
    @{status='measured_fixture_container_health_only';http_status=200;image_id=$taskDigest;checked_at=(Get-Date).ToUniversalTime().ToString('o');limitations='未證明 GPU、真實模型、MLflow、Prometheus 或完整產品 Compose。'} | ConvertTo-Json | Set-Content -LiteralPath 'reports/container-health.json' -Encoding utf8
    Write-Host 'Fixture 容器健康實跑通過；真實 GPU Compose 仍須另行驗證。'
} finally { Pop-Location }
