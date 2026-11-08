# 本地一键检查：前端测试 + 构建 + 后端测试
# 用法：powershell -ExecutionPolicy Bypass -File scripts/check.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host '== npm test ==' -ForegroundColor Cyan
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "npm test failed ($LASTEXITCODE)" }

    Write-Host '== npm run build ==' -ForegroundColor Cyan
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }

    Write-Host '== pytest ==' -ForegroundColor Cyan
    $py = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "missing venv: $py (see server/README.md)" }
    Push-Location (Join-Path $root 'server')
    try {
        & $py -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "pytest failed ($LASTEXITCODE)" }
    } finally { Pop-Location }

    Write-Host 'all checks passed' -ForegroundColor Green
} finally {
    Pop-Location
}
