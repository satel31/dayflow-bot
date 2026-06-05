$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SchedulePath = Join-Path $ProjectRoot "data\celerybeat-schedule"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SchedulePath) | Out-Null
Set-Location $ProjectRoot

& "$ProjectRoot\venv\Scripts\celery.exe" `
    -A dayflow.celery_app.celery_app `
    beat `
    --loglevel=info `
    --schedule $SchedulePath `
    @args
exit $LASTEXITCODE
