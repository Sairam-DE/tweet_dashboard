param(
    [string]$TaskName = "PulseBoardAutoStart"
)

$ErrorActionPreference = "Stop"

schtasks /Delete /TN $TaskName /F | Out-Host
Write-Host "Task '$TaskName' removed."
