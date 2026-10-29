#!/usr/bin/env bash
# Run a pipeline step so that a failure is RECORDED instead of swallowed.
#
# WHY THIS EXISTS
# ---------------
# The workflows carried 104 steps shaped like:
#
#     python resolve_nfl_results.py || echo "resolver failed"
#
# `|| echo` makes the shell succeed, so the step succeeds, so the job
# succeeds, so the run is green. The grader can fail every night for a week
# and the only trace is one line buried in a collapsed log. That is how "no
# games resolved yesterday" and "no prime props today" happened — the
# pipeline reported success while doing nothing.
#
# Removing the `|| echo` outright is the wrong fix: a flaky optional scraper
# would then abort every later step in the job, including the graders and
# writers that matter more than it does. The problem was never that the
# pipeline continued. The problem is that it continued QUIETLY and then
# claimed success.
#
# So: run the command, let the job carry on, and record the failure where it
# cannot be missed — the GitHub step summary, the log with a marker, and a
# tally file. A gate step at the end of the job reads that tally and fails
# the run, so every step still executes but a run with a failed step never
# shows a green check.
#
# USAGE
#   $GITHUB_WORKSPACE/.github/scripts/run_step.sh python foo.py --flag
#   $GITHUB_WORKSPACE/.github/scripts/run_step.sh --label "prop grader" \
#       python resolve_nfl_props_espn.py --lookback 3
#
# Then once per job, after the steps:
#   $GITHUB_WORKSPACE/.github/scripts/run_step.sh --gate
#
# Always exits 0 except for --gate, which exits 1 when anything failed.

set -uo pipefail

TALLY="${STEP_FAILURE_TALLY:-${RUNNER_TEMP:-/tmp}/pipeline_step_failures}"
FINDINGS="${TALLY}_findings"

_summary() {
  # GITHUB_STEP_SUMMARY is absent when running locally; fall back to stdout.
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$1" >> "$GITHUB_STEP_SUMMARY"
  fi
}

if [ "${1:-}" = "--gate" ]; then
  # Findings first: reported, never fatal. A nightly that finds things is a
  # nightly that is working.
  if [ -s "$FINDINGS" ]; then
    fcount=$(wc -l < "$FINDINGS" | tr -d ' ')
    echo "::notice::${fcount} detector finding(s) — reported, not failures:"
    # shellcheck disable=SC2162
    while IFS= read line; do
      echo "::notice::  - ${line}"
    done < "$FINDINGS"
    _summary ""
    _summary "### 🔎 ${fcount} detector finding(s)"
    _summary ""
    _summary "Detectors exited non-zero because they found something. This is"
    _summary "normal operation, not a failed run — see each step's log."
    _summary ""
    _summary '```'
    cat "$FINDINGS" >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>/dev/null || true
    _summary '```'
  fi
  if [ -s "$TALLY" ]; then
    count=$(wc -l < "$TALLY" | tr -d ' ')
    echo "::error::${count} pipeline step(s) failed. This run did real work but"
    echo "::error::is NOT a success. Failures:"
    # shellcheck disable=SC2162
    while IFS= read line; do
      echo "::error::  - ${line}"
    done < "$TALLY"
    _summary ""
    _summary "### ❌ ${count} step(s) failed"
    _summary ""
    _summary '```'
    cat "$TALLY" >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>/dev/null || true
    _summary '```'
    # ══ 2026-10-05 · PUBLISH *WHICH* STEP FAILED, NOT JUST THAT ONE DID ══
    # Andy: "seeing recent workflow failures with nightly and ncaab failing,
    # why?" — and nobody could answer without opening the Actions UI, because
    # the only record of WHICH step failed was a GitHub log that expires.
    # workflow_heartbeat.meta was empty on all 200 recent end-events, so the
    # database knew a run happened and nothing about what went wrong.
    #
    # Smoke-testing every nightly step by hand found all of them passing, so
    # the failing step is intermittent — exactly the case where a durable
    # record is the only way to ever catch it. Post the tally where we can
    # query it. Best-effort: never let telemetry fail the gate.
    if [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_KEY:-}" ]; then
      # Build the payload in python, not sed/awk. The first version escaped
      # quotes by hand and produced "[]" for a tally containing a label with a
      # double quote in it — a telemetry bug that hides the very failure it is
      # meant to report. json.dumps cannot get this wrong.
      python3 - "$TALLY" <<'PYJSON' > /tmp/_sl_stepfail.json 2>/dev/null || true
import json, os, sys
try:
    lines = [l.rstrip('
') for l in open(sys.argv[1], encoding='utf-8', errors='replace') if l.strip()]
except Exception:
    lines = []
print(json.dumps({'workflow': os.environ.get('GITHUB_WORKFLOW', 'unknown'),
                  'event': 'step_failures',
                  'run_id': os.environ.get('GITHUB_RUN_ID', 'local'),
                  'meta': {'count': len(lines), 'failed': lines}}))
PYJSON
      if [ -s /tmp/_sl_stepfail.json ]; then
        curl -sf -X POST "$SUPABASE_URL/rest/v1/workflow_heartbeat"           -H "apikey: $SUPABASE_KEY"           -H "Authorization: Bearer $SUPABASE_KEY"           -H "Content-Type: application/json"           -H "Prefer: return=minimal"           --data-binary @/tmp/_sl_stepfail.json >/dev/null 2>&1 || true
      fi
    fi
    exit 1
  fi
  if [ -s "$FINDINGS" ]; then
    echo "no step failed (detector findings above are informational)"
  else
    echo "all steps reported success"
  fi
  exit 0
fi

DETECTOR=0
MLB_ONLY=0
LABEL=""
while true; do
  case "${1:-}" in
    --detector) DETECTOR=1; shift ;;
    --mlb-only) MLB_ONLY=1; shift ;;
    --label)    LABEL="$2"; shift 2 ;;
    *)          break ;;
  esac
done
[ -n "$LABEL" ] || LABEL="$*"

# 2026-10-29 . SKIP MLB-ONLY WORK WHEN THERE IS NO MLB SLATE, AND SAY SO.
# Measured from 14 days of start/end heartbeats: mlb_pipeline runs 48x at
# 57.5 min mean and mlb_grade_overnight 42x at 19.5 min, so MLB is 255 of
# the 477 workflow minutes we spend per day -- 53% -- for a sport whose last
# game was 2026-10-28. The money (~$61 of $114/month) is the small half. The
# real cost is 25 MLB-specific steps running GREEN AND EMPTY for four
# months, so by spring nobody knows which still work
# (feedback_green_step_that_writes_nothing).
#
# So a skip is RECORDED as 'step_skipped', never as a success. The gate
# probes mlb_game_context for today/tomorrow ET and fails OPEN -- no
# credentials, a non-200, an unreadable count, all mean RUN, because a gate
# must never be the reason a live sport goes dark
# (feedback_weather_pullers_silent_noop).
#
# Cached per job: the probe runs once, not once per step.
if [ "$MLB_ONLY" = "1" ]; then
  _gate_cache="${RUNNER_TEMP:-/tmp}/_mlb_slate_gate"
  if [ ! -f "$_gate_cache" ]; then
    if python mlb_slate_gate.py --quiet; then echo run > "$_gate_cache"
    else echo skip > "$_gate_cache"; fi
  fi
  if [ "$(cat "$_gate_cache" 2>/dev/null)" = "skip" ]; then
    echo "::notice::SKIPPED (no MLB slate): ${LABEL}"
    _summary "- MLB-only step skipped, no slate: \`${LABEL}\`"
    if [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_KEY:-}" ]; then
      _hbs="$(mktemp)"
      STEP_LABEL="$LABEL" python3 - <<'PYSK' > "$_hbs" 2>/dev/null || true
import json, os
print(json.dumps({'workflow': os.environ.get('GITHUB_WORKFLOW','unknown'),
                  'event': 'step_skipped',
                  'run_id': os.environ.get('GITHUB_RUN_ID','local'),
                  'meta': {'label': os.environ.get('STEP_LABEL',''),
                           'reason': 'no MLB slate (off-season)'}}))
PYSK
      if [ -s "$_hbs" ]; then
        curl -sf -m 10 -X POST "${SUPABASE_URL}/rest/v1/workflow_heartbeat"           -H "apikey: ${SUPABASE_KEY}"           -H "Authorization: Bearer ${SUPABASE_KEY}"           -H "Content-Type: application/json"           -H "Prefer: return=minimal"           --data-binary @"$_hbs" >/dev/null 2>&1 || true
      fi
      rm -f "$_hbs"
    fi
    exit 0
  fi
fi

start=$(date +%s)

# ══ 2026-09-30 · WHY THE TWO BRANCHES ══
# Only a --detector step needs its output captured, because only a detector
# needs the traceback test below. Capturing costs something real, so the ~100
# existing steps must NOT pay it: piping through `tee` replaces the child's
# stdout with a pipe, which makes Python switch from line to BLOCK buffering
# (output stops streaming and arrives in one lump at the end) and merges
# stderr into stdout, changing interleaving in every workflow log.
#
# So the default path runs "$@" exactly as it did before this flag existed —
# byte-identical behaviour for every step that does not opt in.
if [ "$DETECTOR" = "1" ]; then
  _out="$(mktemp)"
  "$@" 2>&1 | tee "$_out"
  rc=${PIPESTATUS[0]}
  dur=$(( $(date +%s) - start ))
  # A detector's exit 1 is a FINDING unless it actually crashed. Anything >= 2,
  # or a traceback in the output, is a real failure whatever the flag says.
  if [ "$rc" -eq 1 ] &&
     ! grep -q 'Traceback (most recent call last)' "$_out"; then
    msg="${LABEL} — findings (exit 1 after ${dur}s)"
    echo "::notice::DETECTOR FINDING: ${msg}"
    mkdir -p "$(dirname "$FINDINGS")"
    printf '%s\n' "$msg" >> "$FINDINGS"
    _summary "- 🔎 \`${LABEL}\` findings (${dur}s)"
    rm -f "$_out"
    exit 0
  fi
  rm -f "$_out"
else
  # ══ 2026-10-29 · CAPTURE *WHY*, NOT JUST THAT IT FAILED ══════════════
  # The heartbeat recorded label + rc + duration and nothing else, so
  # "generate_ncaaf_game_reads.py — exit 1 after 2s" could only be explained
  # by Andy pasting the log. It cost two rounds today: the cause was an
  # expired ANTHROPIC_API_KEY, visible as a 401 traceback in the step log and
  # absent from everything queryable.
  #
  # stdout is deliberately NOT touched. The note above is right that piping
  # it flips Python to block buffering and merges the streams, and ~100 steps
  # must not pay that. STDERR is where a traceback goes, so only stderr is
  # captured — and through `tee` so it still STREAMS live, which matters
  # because this repo has had steps hang (price_picks, 10-23) and a hang is
  # undebuggable if its output only appears at exit.
  #
  # 2026-10-29 (same day, second attempt) . THE FIRST VERSION RACED.
  # It was `"$@" 2> >(tee -a "$_err" >&2)`. Bash does not wait for a process
  # substitution, so the heartbeat below read $_err while tee was still
  # buffering, and `stderr_tail` came back EMPTY on every step_failed row it
  # was supposed to explain -- including the four audit_read_pick_consistency
  # failures on 10-29 that then could not be diagnosed. Telemetry that
  # reports nothing is worse than none, because it looks like it is working.
  #
  # A FIFO with a real PID fixes it and keeps the live streaming the note
  # above argues for: tee reads the pipe and forwards to stderr as output
  # arrives, the command's exit closes the write end, tee sees EOF and
  # exits, and `wait` guarantees the file is complete before it is read.
  _err="$(mktemp)"
  _fifo="$(mktemp -u)"
  if mkfifo "$_fifo" 2>/dev/null; then
    tee -a "$_err" >&2 < "$_fifo" &
    _teepid=$!
    "$@" 2>"$_fifo"
    rc=$?
    wait "$_teepid" 2>/dev/null || true
    rm -f "$_fifo"
  else
    # No FIFO available (rare). Capture without streaming rather than race:
    # a complete tail matters more than seeing it live.
    "$@" 2>"$_err"
    rc=$?
    [ -s "$_err" ] && cat "$_err" >&2
  fi
  dur=$(( $(date +%s) - start ))
fi

if [ "$rc" -ne 0 ]; then
  msg="${LABEL} — exit ${rc} after ${dur}s"
  echo "::warning::STEP FAILED: ${msg}"
  mkdir -p "$(dirname "$TALLY")"
  printf '%s\n' "$msg" >> "$TALLY"
  _summary "- ❌ \`${LABEL}\` exit ${rc} (${dur}s)"
  # ══ 2026-09-28 · RECORD THE FAILURE WHERE IT CAN BE QUERIED ══
  # The tally file lives on the runner and dies with it, and the ::error::
  # lines live in a collapsed log. So "daily_card and nfl_pipeline are
  # failing" could only be answered by re-running nineteen steps by hand to
  # find which one broke — which is what happened today.
  #
  # workflow_heartbeat already exists with a JSONB meta column and is already
  # written by these workflows, so this needs no migration. One row per failed
  # step makes the question answerable in a single query:
  #
  #   select fired_at, workflow, meta->>'label', meta->>'rc'
  #   from workflow_heartbeat where event = 'step_failed'
  #   order by fired_at desc;
  #
  # Best-effort and silent: this is diagnostics, and a logging hiccup must
  # never change a step's outcome.
  if [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_KEY:-}" ]; then
    # Build the payload in python so the captured stderr — which contains
    # quotes, newlines and whatever a traceback holds — cannot break the
    # JSON. The --gate branch above already learned this the hard way: a
    # hand-rolled escape produced "[]" for a label containing a quote, a
    # telemetry bug that hid the failure it existed to report.
    _hb="$(mktemp)"
    STEP_LABEL="$LABEL" STEP_RC="$rc" STEP_DUR="$dur" \
    python3 - "${_err:-/dev/null}" <<'PYHB' > "$_hb" 2>/dev/null || true
import json, os, sys
tail = ''
try:
    with open(sys.argv[1], encoding='utf-8', errors='replace') as fh:
        # Last 1200 chars: a traceback's final lines name the exception,
        # which is the part that identifies the cause.
        tail = fh.read()[-1200:]
except Exception:
    pass
print(json.dumps({
    'workflow': os.environ.get('GITHUB_WORKFLOW', 'unknown'),
    'event': 'step_failed',
    'run_id': os.environ.get('GITHUB_RUN_ID', 'local'),
    'meta': {'label': os.environ.get('STEP_LABEL', ''),
             'rc': int(os.environ.get('STEP_RC') or 0),
             'duration_s': int(os.environ.get('STEP_DUR') or 0),
             'stderr_tail': tail},
}))
PYHB
    if [ -s "$_hb" ]; then
      curl -sf -m 10 -X POST "${SUPABASE_URL}/rest/v1/workflow_heartbeat" \
        -H "apikey: ${SUPABASE_KEY}" \
        -H "Authorization: Bearer ${SUPABASE_KEY}" \
        -H "Content-Type: application/json" \
        -H "Prefer: return=minimal" \
        --data-binary @"$_hb" >/dev/null 2>&1 || true
    fi
    rm -f "$_hb"
  fi
else
  echo "ok: ${LABEL} (${dur}s)"
fi
# The stderr capture is a temp file on the runner; do not leak one per step.
rm -f "${_err:-}" 2>/dev/null || true

# Deliberately 0 — later steps in the job must still run. The --gate step is
# what turns the run red.
exit 0
