# Builds the sandbox image from Dockerfile.sandbox (P5).
#
#   powershell -File tfl_deep_agent/sandbox/build.ps1 [-Image tfl-sandbox:latest]
#
# The Dockerfile goes in on stdin with no build context, so no repository file ends up in the image.
param(
    [string]$Image = "tfl-sandbox:latest"
)
$ErrorActionPreference = "Stop"
$dockerfile = Join-Path $PSScriptRoot "Dockerfile.sandbox"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not on PATH"
    exit 1
}
# the Dockerfile is ASCII; pipe it as UTF-8 without BOM
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::ReadAllText($dockerfile) | docker build -t $Image -
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker build failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Output "built $Image"
