@echo off
REM ===========================================================================
REM  Local cross-sport line poller - Windows Task Scheduler entry point.
REM
REM  WHY (2026-10-05)
REM  multisport_line_poller.yml is scheduled 37x/day and lands a fraction of
REM  that. Counted by distinct capture minute in line_history:
REM
REM      NBA   0-1 per day      NCAAF  2-9 per day
REM      NFL   5-12 per day     NHL    6-11 per day
REM
REM  NBA's regular season opens 2026-10-21. At one snapshot a day it would
REM  launch with no usable pre-game price history, which is the exact problem
REM  that left 484 Jerry reads unpriceable. Same fix as the MLB poller, which
REM  went from 5 percent landing to 100 percent on Task Scheduler.
REM
REM  ORDER IS LOAD-BEARING. The odds pulls must precede movement detection,
REM  and classification must follow detection, or each step reads the previous
REM  step's stale values. Pruning runs LAST so it never deletes rows the
REM  detectors in this same run still need.
REM
REM  PRUNE IS DELIBERATELY NOT RUN HERE. The GitHub workflow calls
REM  prune_line_history --days 14 --apply, and that TTL is why every sport's
REM  earliest capture is exactly 13-14 days old. Running it 96x/day instead of
REM  37x would not change what it deletes, but pruning is a destructive step
REM  and it does not belong on a high-frequency timer. The workflow keeps it.
REM
REM  Register with register_multisport_poller_task.ps1.
REM  Secrets come from mlb_pipeline\.env and are never printed.
REM
REM  NOTE: no carets or percent signs in this comment block - a trailing caret
REM  inside REM acts as a line continuation in cmd.
REM ===========================================================================

setlocal
set "REPO=%~dp0"
cd /d "%REPO%" || exit /b 1

set "PYTHONIOENCODING=utf-8"
set "PY=C:\Python314\python.exe"
set "LOG=%REPO%_multisport_poller_local.log"


REM NO LOCK FILE ON PURPOSE. The task is registered with
REM MultipleInstances=IgnoreNew, which already guarantees no overlap and
REM cannot go stale. The first version kept its own lock file; a run that
REM got killed left the lock behind and would have blocked EVERY future
REM run silently. A guarantee the OS already gives is not worth a
REM deadlock we have to clear by hand.

echo. >> "%LOG%"
echo ======== [%DATE% %TIME%] multisport poller start ======== >> "%LOG%"

set "FAILED="

call :step nhl_odds_pull.py
call :step nba_odds_pull.py
call :step nfl_odds_pull.py
call :step ncaaf_odds_pull.py
call :step ncaab_odds_pull.py

REM Routed through a CALL subroutine, not an inline parenthesised block.
REM Inside `for ... do ( ... )` cmd expands %FAILED% ONCE at parse time, so
REM `set "FAILED=%FAILED% detect:%%S"` cannot accumulate - a failing detect
REM step would have been silently swallowed, which is the exact class of bug
REM run_step.sh exists to prevent. CALL gives each iteration a fresh
REM expansion context.
for %%S in (NHL NBA NFL NCAAF NCAAB) do call :detect %%S

echo -- classify_line_moves ALL >> "%LOG%"
"%PY%" classify_line_moves.py --sport ALL >> "%LOG%" 2>&1
if errorlevel 1 set "FAILED=%FAILED% classify"

if defined FAILED (
  echo ======== [%DATE% %TIME%] FAILED STEPS:%FAILED% ======== >> "%LOG%"
) else (
  echo ======== [%DATE% %TIME%] all steps ok ======== >> "%LOG%"
)

endlocal & exit /b 0

:step
echo -- %~1 >> "%LOG%"
"%PY%" %~1 >> "%LOG%" 2>&1
if errorlevel 1 set "FAILED=%FAILED% %~1"
goto :eof

:detect
echo -- detect_line_movement %~1 >> "%LOG%"
"%PY%" detect_line_movement.py --sport %~1 --lookback-hours 6 >> "%LOG%" 2>&1
if errorlevel 1 set "FAILED=%FAILED% detect:%~1"
goto :eof
