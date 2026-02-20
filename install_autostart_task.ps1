param(
    [string]$TaskName = "PulseBoardAutoStart",
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$startScript = Join-Path $PSScriptRoot "start_site.ps1"
if (-not (Test-Path $startScript)) {
    throw "Start script not found: $startScript"
}

$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`" -BindHost `"$BindHost`" -Port $Port"

schtasks /Create /SC ONLOGON /TN $TaskName /TR $taskCommand /F | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task '$TaskName'. Try running this script as Administrator."
}

schtasks /Run /TN $TaskName | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Task created, but immediate start failed. It will still run automatically at your next login."
}

Write-Host ""
Write-Host "Task '$TaskName' installed and started."
Write-Host "Use this URL: http://localhost:$Port"
Write-Host "From other devices on same Wi-Fi: http://<your-computer-ip>:$Port"
