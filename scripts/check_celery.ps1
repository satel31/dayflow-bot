$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& "$ProjectRoot\venv\Scripts\python.exe" -m dayflow.celery_health
exit $LASTEXITCODE
