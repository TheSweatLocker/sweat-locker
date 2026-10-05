# Register the local MLB line poller with Windows Task Scheduler.
#
# WHY (2026-10-05)
# mlb_line_poller.yml is scheduled 72x/day on GitHub Actions and lands 3-4
# times (~5%). GitHub refuses the runners and the workflow shows no failure
# because the job never starts. line_poller is the sole writer of
# `line_history`, the table that prices every pick for ROI and CLV, so at 5%
# landing most games get no pre-game capture.
#
# This runs the SAME Python on a host that actually fires it - no port of the
# 391 lines of consensus math and close_total locking.
#
# Idempotent: re-running replaces the existing task.
#
#   powershell -ExecutionPolicy Bypass -File register_line_poller_task.ps1
#   powershell -ExecutionPolicy Bypass -File register_line_poller_task.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$TaskName = 'SweatLocker\line_poller'
$Script   = 'C:\Users\gomez\SweatShop\mlb_pipeline\run_line_poller.cmd'

if ($Remove) {
    schtasks /Delete /TN $TaskName /F
    Write-Output "removed $TaskName"
    exit 0
}

if (-not (Test-Path $Script)) { throw "runner not found: $Script" }

# /SC MINUTE /MO 15 repeats indefinitely, which is what we want - the GitHub
# schedule it replaces was every 15 minutes across an 18h window.
# /RL LIMITED: the poller needs no elevation. It reads .env and makes HTTPS
# calls; granting it admin would be privilege we do not need.
schtasks /Create /TN $TaskName /SC MINUTE /MO 15 /TR "`"$Script`"" /RL LIMITED /F
if ($LASTEXITCODE -ne 0) { throw "schtasks create failed ($LASTEXITCODE)" }

# Never let two copies overlap - two pollers writing the same minute would
# double-count captures in the exact metric used to verify this works.
$t = Get-ScheduledTask -TaskName 'line_poller' -TaskPath '\SweatLocker\'
$t.Settings.MultipleInstances      = 'IgnoreNew'
$t.Settings.StartWhenAvailable     = $true      # catch up after sleep
$t.Settings.ExecutionTimeLimit     = 'PT10M'    # a hung poll must not block the next
$t.Settings.DisallowStartIfOnBatteries = $false
$t.Settings.StopIfGoingOnBatteries     = $false
$t | Set-ScheduledTask | Out-Null

Write-Output "registered $TaskName"
Get-ScheduledTask -TaskName 'line_poller' -TaskPath '\SweatLocker\' |
    Select-Object TaskName, State,
        @{n='Interval';e={$_.Triggers[0].Repetition.Interval}},
        @{n='MultipleInstances';e={$_.Settings.MultipleInstances}},
        @{n='TimeLimit';e={$_.Settings.ExecutionTimeLimit}} |
    Format-List
