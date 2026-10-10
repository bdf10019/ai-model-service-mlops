$ErrorActionPreference = 'Stop'
$ProductRoot = Split-Path -Parent $PSScriptRoot
$ShowcaseRoot = Join-Path $ProductRoot 'showcase'

function Invoke-Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Showcase verification command failed with exit code $LASTEXITCODE" }
}

Invoke-Checked { python (Join-Path $PSScriptRoot 'build_showcase_data.py') }
Invoke-Checked { python (Join-Path $PSScriptRoot 'showcase_privacy_check.py') }
Push-Location $ShowcaseRoot
try {
    Invoke-Checked { pnpm build }
    Invoke-Checked { pnpm test:report }
    Invoke-Checked { pnpm test:e2e }
} finally {
    Pop-Location
}
Invoke-Checked { python (Join-Path $PSScriptRoot 'showcase_evidence_index.py') }
