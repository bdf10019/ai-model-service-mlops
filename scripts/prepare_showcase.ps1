$ErrorActionPreference = 'Stop'
$ProductRoot = Split-Path -Parent $PSScriptRoot
python (Join-Path $PSScriptRoot 'build_showcase_data.py')
Push-Location (Join-Path $ProductRoot 'showcase')
try {
    pnpm install --offline --frozen-lockfile
    pnpm build
} finally {
    Pop-Location
}
