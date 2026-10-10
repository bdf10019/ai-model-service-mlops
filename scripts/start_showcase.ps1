$ErrorActionPreference = 'Stop'
$ProductRoot = Split-Path -Parent $PSScriptRoot
$ShowcaseRoot = Join-Path $ProductRoot 'showcase'
if (-not (Test-Path (Join-Path $ShowcaseRoot 'dist\index.html'))) {
    & (Join-Path $PSScriptRoot 'prepare_showcase.ps1')
}
Push-Location $ShowcaseRoot
try {
    pnpm preview
} finally {
    Pop-Location
}
