$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& "$ProjectRoot\venv\Scripts\celery.exe" `
    -A dayflow.celery_app.celery_app `
    worker `
    --loglevel=info `
    --pool=solo `
    @args
exit $LASTEXITCODE
