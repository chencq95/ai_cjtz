[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "DataMarketProbe-Daily"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($null -eq (Get-Command "Get-ScheduledTask" -ErrorAction SilentlyContinue) -or
    $null -eq (Get-Command "Unregister-ScheduledTask" -ErrorAction SilentlyContinue)) {
    throw "Windows ScheduledTasks commands are unavailable on this host."
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $Task) {
    Write-Output ("Task '{0}' is not installed; nothing was changed." -f $TaskName)
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output ("Removed task '{0}'. Project files, database, logs, and .venv were preserved." -f $TaskName)
    Write-Output "Rerun scripts\install_windows_task.ps1 to restore the task."
}
