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

_summary() {
  # GITHUB_STEP_SUMMARY is absent when running locally; fall back to stdout.
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$1" >> "$GITHUB_STEP_SUMMARY"
  fi
}

if [ "${1:-}" = "--gate" ]; then
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
    exit 1
  fi
  echo "all steps reported success"
  exit 0
fi

LABEL=""
if [ "${1:-}" = "--label" ]; then
  LABEL="$2"
  shift 2
fi
[ -n "$LABEL" ] || LABEL="$*"

start=$(date +%s)
"$@"
rc=$?
dur=$(( $(date +%s) - start ))

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
    _esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
    curl -sf -m 10 -X POST "${SUPABASE_URL}/rest/v1/workflow_heartbeat" \
      -H "apikey: ${SUPABASE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_KEY}" \
      -H "Content-Type: application/json" \
      -H "Prefer: return=minimal" \
      -d "{\"workflow\":\"${GITHUB_WORKFLOW:-unknown}\",\"event\":\"step_failed\",\"run_id\":\"${GITHUB_RUN_ID:-}\",\"meta\":{\"label\":\"$(_esc "$LABEL")\",\"rc\":${rc},\"duration_s\":${dur}}}" \
      >/dev/null 2>&1 || true
  fi
else
  echo "ok: ${LABEL} (${dur}s)"
fi

# Deliberately 0 — later steps in the job must still run. The --gate step is
# what turns the run red.
exit 0
