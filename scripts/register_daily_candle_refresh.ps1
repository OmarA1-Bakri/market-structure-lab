[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = "Market Structure Lab - Daily Candle Refresh",
    [ValidatePattern("^\d{2}:\d{2}$")]
    [string]$DailyAt = "07:15",
    [string]$PowerShellExecutable = "C:\Program Files\PowerShell\7\pwsh.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$runner = Join-Path $resolvedProjectRoot "scripts/run_daily_candle_refresh.ps1"
$postgresVersion = Join-Path $resolvedProjectRoot "data/postgres/PG_VERSION"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "daily candle refresh runner is missing: $runner"
}
if (-not (Test-Path -LiteralPath $PowerShellExecutable -PathType Leaf)) {
    throw "PowerShell 7 executable is missing: $PowerShellExecutable"
}
if (-not (Test-Path -LiteralPath $postgresVersion -PathType Leaf)) {
    throw "durable PostgreSQL has not been initialized; refusing to register the daily task"
}

$time = [DateTime]::ParseExact(
    $DailyAt,
    "HH:mm",
    [System.Globalization.CultureInfo]::InvariantCulture
)
$arguments = '-NoLogo -NoProfile -NonInteractive -File "{0}" -ProjectRoot "{1}"' -f (
    $runner,
    $resolvedProjectRoot
)
$action = New-ScheduledTaskAction `
    -Execute $PowerShellExecutable `
    -Argument $arguments `
    -WorkingDirectory $resolvedProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Refresh checksum-pinned cryptocurrency candles without bootstrap or snapshots."

if ($PSCmdlet.ShouldProcess($TaskName, "Register or update daily candle refresh task at $DailyAt")) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName
}
