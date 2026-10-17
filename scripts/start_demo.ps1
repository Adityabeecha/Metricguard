$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Create the Python environment and install requirements first. See README.md.'
}
if (-not (Test-Path -LiteralPath 'apps\web\dist\index.html')) {
    throw 'Build the web interface with npm run build --prefix apps/web. See README.md.'
}
& .\.venv\Scripts\python.exe -m scripts.seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo evidence generation failed.' }
& .\.venv\Scripts\python.exe -m uvicorn services.api.app:app --host 127.0.0.1 --port 8000
