#requires -Version 5.1
<#
.SYNOPSIS
    Register or remove the StatusCast daemon logon task.

.DESCRIPTION
    Creates a per-user Scheduled Task that starts scripts/run_daemon.ps1 hidden
    at logon. The daemon writes logs to run\statuscast.log, so no console window
    is needed. Registration does not require elevation for a current-user task;
    if your policy blocks it, run PowerShell as administrator once.

.PARAMETER Uninstall
    Remove the scheduled task instead of creating it.

.PARAMETER TaskName
    Scheduled Task name. Defaults to StatusCast.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_task.ps1 -WhatIf

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_task.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_task.ps1 -Uninstall
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Uninstall,
    [string]$TaskName = 'StatusCast'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $PSScriptRoot 'run_daemon.ps1'

if ($Uninstall) {
    if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
        try {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
            Write-Output "Removed scheduled task: $TaskName"
        }
        catch {
            Write-Output "Scheduled task not removed (it may not exist): $($_.Exception.Message)"
        }
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $RunScript -PathType Leaf)) {
    [Console]::Error.WriteLine("run_daemon.ps1 not found: $RunScript")
    exit 3
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Register logon scheduled task')) {
    $Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`""
    $Action = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument $Arguments `
        -WorkingDirectory $ProjectRoot
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero)

    try {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $Action `
            -Trigger $Trigger `
            -Settings $Settings `
            -Description 'StatusCast iPhone status bridge daemon' `
            -Force | Out-Null
        Write-Output "Registered scheduled task: $TaskName"
        Write-Output "Command: powershell.exe $Arguments"
        Write-Output "Log: $ProjectRoot\run\statuscast.log"
    }
    catch {
        [Console]::Error.WriteLine("Failed to register scheduled task: $($_.Exception.Message)")
        [Console]::Error.WriteLine("If policy blocks it, run this script once from an elevated PowerShell.")
        exit 4
    }
}
