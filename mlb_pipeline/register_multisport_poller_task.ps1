# Register the local cross-sport line poller with Windows Task Scheduler.
#
# WHY (2026-10-05)
# multisport_line_poller.yml is scheduled 37x/day and lands a fraction:
# NBA 0-1, NCAAF 2-9, NFL 5-12, NHL 6-11 per day. NBA's regular season opens
# 2026-10-21 and at one snapshot a day it would launch with no usable pre-game
# price history - the same gap that left 484 Jerry reads unpriceable.
#
# Same fix as the MLB poller, which went 5% -> 100% landing on Task Scheduler.
# Runs the identical Python; nothing is ported.
#
# Every 30 minutes, matching the GitHub cadence in its busy window. Five Odds
# API calls per run, so ~240/day against a ~5M quota.
#
# Idempotent: re-running replaces the task.
#
#   powershell -ExecutionPolicy Bypass -File register_multisport_poller_task.ps1
#   powershell -ExecutionPolicy Bypass -File register_multisport_poller_task.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$TaskName = 'SweatLocker\multisport_poller'
$Script   = 'C:\Users\gomez\SweatShop\mlb_pipeline\run_multisport_poller.cmd'

if ($Remove) {
    schtasks /Delete /TN $TaskName /F
    Write-Output "removed $TaskName"
    exit 0
}

if (-not (Test-Path $Script)) { throw "runner not found: $Script" }

schtasks /Create /TN $TaskName /SC MINUTE /MO 30 /TR "`"$Script`"" /RL LIMITED /F
if ($LASTEXITCODE -ne 0) { throw "schtasks create failed ($LASTEXITCODE)" }

$t = Get-ScheduledTask -TaskName 'multisport_poller' -TaskPath '\SweatLocker\'
# This run is 11 sequential python steps, so it needs a longer cap than the
# MLB poller and must never overlap itself.
$t.Settings.MultipleInstances          = 'IgnoreNew'
$t.Settings.StartWhenAvailable         = $true
$t.Settings.ExecutionTimeLimit         = 'PT20M'
$t.Settings.DisallowStartIfOnBatteries = $false
$t.Settings.StopIfGoingOnBatteries     = $false
$t | Set-ScheduledTask | Out-Null

Write-Output "registered $TaskName"
Get-ScheduledTask -TaskPath '\SweatLocker\' |
    Select-Object TaskName, State,
        @{n='Interval';e={$_.Triggers[0].Repetition.Interval}},
        @{n='TimeLimit';e={$_.Settings.ExecutionTimeLimit}} |
    Format-Table -AutoSize
