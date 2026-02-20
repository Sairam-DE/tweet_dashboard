param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = Split-Path -Parent $projectRoot
$pythonPath = Join-Path $workspaceRoot ".venv\\Scripts\\python.exe"

if (-not (Test-Path $pythonPath)) {
    throw "Python venv not found at $pythonPath"
}

Set-Location $projectRoot

if (-not $env:DJANGO_ALLOWED_HOSTS) {
    $env:DJANGO_ALLOWED_HOSTS = "*"
}

if (-not $env:DJANGO_DEBUG) {
    $env:DJANGO_DEBUG = "true"
}

Write-Host "PulseBoard start requested on http://$BindHost`:$Port"
Write-Host "Project: $projectRoot"

& $pythonPath manage.py migrate --noinput

while ($true) {
    try {
        & $pythonPath manage.py runserver "$BindHost`:$Port" --noreload
    } catch {
        Write-Host "Server stopped with error: $($_.Exception.Message)"
    }
    Write-Host "Restarting in 3 seconds..."
    Start-Sleep -Seconds 3
}
