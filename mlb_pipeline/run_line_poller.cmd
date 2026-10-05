@echo off
REM ===========================================================================
REM  Local line poller runner - Windows Task Scheduler entry point.
REM
REM  WHY THIS EXISTS (2026-10-05)
REM  mlb_line_poller.yml is scheduled 72x/day on GitHub Actions and LANDS 3-4
REM  times, about 5 percent. GitHub refuses the runners - "The job was not
REM  acquired by Runner of type hosted" - and the workflow reports no failure
REM  because the job never starts. Measured by distinct capture minute in
REM  line_history: 10-01 4/72, 10-03 4/72, 10-04 4/72, 10-05 3/72.
REM
REM  line_poller is the SOLE writer of line_history, which prices every pick
REM  for ROI and CLV. At 5 percent landing most games get no pre-game capture,
REM  which is why 484 of 528 unpriced Jerry reads looked like they predated the
REM  feature. They did not. The collector just was not running.
REM
REM  Porting 391 lines of consensus math and close_total locking to a Deno
REM  edge function mid-playoffs is the riskiest option, not the safest. This
REM  runs the SAME Python on a host that actually fires it. Zero port.
REM
REM  Register with register_line_poller_task.ps1.
REM  Secrets come from mlb_pipeline\.env and are never printed.
REM
REM  NOTE: keep this comment block free of carets and percent signs. A
REM  trailing caret inside REM still acts as a line continuation in cmd and
REM  the first version of this file emitted a dozen parser errors per run.
REM ===========================================================================

setlocal
set "REPO=%~dp0"
cd /d "%REPO%" || exit /b 1

set "PYTHONIOENCODING=utf-8"
set "LOG=%REPO%_line_poller_local.log"


REM NO LOCK FILE ON PURPOSE. The task is registered with
REM MultipleInstances=IgnoreNew, which already guarantees no overlap and
REM cannot go stale. The first version kept its own lock file; a run that
REM got killed left the lock behind and would have blocked EVERY future
REM run silently. A guarantee the OS already gives is not worth a
REM deadlock we have to clear by hand.

echo. >> "%LOG%"
echo ======== [%DATE% %TIME%] line_poller start ======== >> "%LOG%"
"C:\Python314\python.exe" line_poller.py >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo ======== [%DATE% %TIME%] exit=%RC% ======== >> "%LOG%"

exit /b %RC%
