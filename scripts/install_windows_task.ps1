[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "DataMarketProbe-Daily",

    [ValidateRange(0, 23)]
    [int]$Hour = 2,

    [ValidateRange(0, 59)]
    [int]$Minute = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$RunScript = Join-Path $PSScriptRoot "run_scheduled.ps1"
$VenvScripts = Join-Path $ProjectRoot ".venv\Scripts"

if (-not (Test-Path -LiteralPath $RunScript -PathType Leaf)) {
    throw "Scheduled runner not found: $RunScript"
}
if (-not (
    (Test-Path -LiteralPath (Join-Path $VenvScripts "dmp.exe") -PathType Leaf) -or
    (Test-Path -LiteralPath (Join-Path $VenvScripts "data-market-probe.exe") -PathType Leaf) -or
    (Test-Path -LiteralPath (Join-Path $VenvScripts "python.exe") -PathType Leaf)
)) {
    throw "Project virtual environment is missing under $VenvScripts. Create and install .venv before registering the task."
}

$requiredCommands = @(
    "New-ScheduledTaskAction",
    "New-ScheduledTaskTrigger",
    "New-ScheduledTaskPrincipal",
    "New-ScheduledTaskSettingsSet",
    "New-ScheduledTask",
    "Register-ScheduledTask"
)
foreach ($commandName in $requiredCommands) {
    if ($null -eq (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Windows ScheduledTasks command is unavailable: $commandName"
    }
}

$PowerShellExecutable = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShellExecutable -PathType Leaf)) {
    $PowerShellExecutable = (Get-Command "pwsh.exe" -ErrorAction Stop).Source
}

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$At = [DateTime]::Today.AddHours($Hour).AddMinutes($Minute)
$ActionArguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Mode incremental' -f $RunScript

$Action = New-ScheduledTaskAction `
    -Execute $PowerShellExecutable `
    -Argument $ActionArguments `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15)
$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Daily incremental crawl for Data Market Probe."

# -Force makes this script idempotent: rerunning it replaces the existing task
# with the newly requested time and current project path.
Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null

Write-Output ("Registered task '{0}' for {1:HH\:mm} daily as {2}." -f $TaskName, $At, $CurrentUser)
Write-Output ("Runner: {0}" -f $RunScript)

