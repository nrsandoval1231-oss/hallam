<#
.SYNOPSIS
  One scheduled run of the Sabra loop. Registered by schedule-windows.ps1; do not edit the
  task's arguments by hand.

.DESCRIPTION
  Loads <PrivateDir>\hallam-env.ps1 (which sets SABRA_PRIVATE_DIR and friends), then runs

      uv run sabra run --json >> <data_dir>\logs\run-<date>.log

  Output of the run, and any error, goes to that log as UTF-8. The exit code is the run's:
  non-zero when the run failed. The script stops with an error (and runs nothing) when
  hallam-env.ps1 is missing: without it the run would silently use the wrong database.
#>
[CmdletBinding()]
param(
    [string]$PrivateDir = "C:\Users\nrsan\hallam-private",
    [string]$Repo = "",
    [switch]$Prepare
)

$ErrorActionPreference = "Stop"

# Not a parameter default: Windows PowerShell 5.1 leaves $PSScriptRoot empty there.
if (-not $Repo) {
    $Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

# A trailing backslash would otherwise be read as an escaped quote on a command line.
$PrivateDir = $PrivateDir.TrimEnd("\", "/")

$envScript = Join-Path $PrivateDir "hallam-env.ps1"
if (-not (Test-Path -LiteralPath $envScript)) {
    throw "Cannot find $envScript. Nothing was run. Create it (it sets SABRA_PRIVATE_DIR) or pass -PrivateDir."
}
. $envScript
if (-not $env:SABRA_PRIVATE_DIR) {
    $env:SABRA_PRIVATE_DIR = $PrivateDir
}

# Windows PowerShell 5.1 otherwise decodes uv's output with the OEM code page.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# Where the engine keeps its runtime files (see Settings.model_post_init).
if ($env:SABRA_DATA_DIR) {
    $dataDir = $env:SABRA_DATA_DIR
} else {
    $dataDir = Join-Path $env:SABRA_PRIVATE_DIR "var"
}
$logDir = Join-Path $dataDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("run-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

Set-Location $Repo
$runArgs = @("run", "sabra", "run", "--json")
if ($Prepare) {
    $runArgs += "--prepare"
}

"=== {0} sabra run ===" -f (Get-Date -Format "s") | Add-Content -Path $log -Encoding utf8
# uv writes progress to stderr; that is not a failure (the exit code decides). Merging the
# streams and turning each line into a plain string keeps the log one encoding (UTF-8) and
# free of PowerShell's NativeCommandError wrappers.
$ErrorActionPreference = "Continue"
& uv @runArgs 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $log -Append -Encoding utf8
$code = $LASTEXITCODE
"=== exit code {0} ===" -f $code | Add-Content -Path $log -Encoding utf8
exit $code
