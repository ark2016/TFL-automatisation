$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Write-Host "Building tfl-lean4 image..."
docker compose -f "$ScriptDir\docker-compose.yml" build lean4
Write-Host "Done. Image: tfl-lean4"
