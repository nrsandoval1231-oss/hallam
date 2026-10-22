<#
.SYNOPSIS
  Registers (or removes) a daily Windows Task Scheduler task that runs the Sabra loop.

.DESCRIPTION
  The task runs scripts\run-sabra-daily.ps1 once a day. That script loads
  <PrivateDir>\hallam-env.ps1, then runs

      uv run sabra run --json >> <data_dir>\logs\run-<date>.log

  The run discovers jobs, ranks them, researches the shortlist and (with -Prepare) prepares
  application packages for the top roles. It never approves or submits anything: you still
  read and approve each package yourself.

  The task runs as you, and only while you are logged on. If the PC is off at the scheduled
  time it runs when you next log on (StartWhenAvailable). A second run never starts while one
  is still going.

  Nothing is registered until you run this script yourself.

.PARAMETER PrivateDir
  The private directory that holds hallam-env.ps1, the database and the logs.

.PARAMETER At
  Time of day to run, 24-hour "HH:mm". Default 06:30.

.PARAMETER Prepare
  Also prepare packages for the top roles (the search's prepare_target).

.PARAMETER TaskName
  Name of the scheduled task. Default "Sabra daily run".

.PARAMETER Unregister
  Remove the task instead of registering it.

.EXAMPLE
  .\scripts\schedule-windows.ps1 -At 06:30 -Prepare

.EXAMPLE
  .\scripts\schedule-windows.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$PrivateDir = "C:\Users\nrsan\hallam-private",
    [string]$At = "06:30",
    [string]$TaskName = "Sabra daily run",
    [switch]$Prepare,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
# A trailing backslash would be read as an escaped quote in the task's argument string.
$PrivateDir = $PrivateDir.TrimEnd("\", "/")
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runner = Join-Path $repo "scripts\run-sabra-daily.ps1"

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Host "No scheduled task named '$TaskName'. Nothing to remove."
        return
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the scheduled task '$TaskName'."
    return
}

if (-not (Test-Path $runner)) {
    throw "Cannot find $runner"
}
if (-not (Test-Path (Join-Path $PrivateDir "hallam-env.ps1"))) {
    Write-Warning "$PrivateDir\hallam-env.ps1 does not exist yet. The task will fail until it does."
}
try {
    $time = [datetime]::ParseExact($At, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)
} catch {
    throw "-At must be a 24-hour time like 06:30, not '$At'."
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -PrivateDir `"$PrivateDir`" -Repo `"$repo`""
if ($Prepare) {
    $arguments += " -Prepare"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Sabra: discover, rank and research jobs (never submits anything)." `
    -Force | Out-Null

Write-Host "Registered '$TaskName' to run daily at $At."
Write-Host "Logs: <data dir>\logs\run-<date>.log (the data dir is <PrivateDir>\var unless hallam-env.ps1 sets SABRA_DATA_DIR)."
Write-Host "Remove it with: .\scripts\schedule-windows.ps1 -Unregister"
