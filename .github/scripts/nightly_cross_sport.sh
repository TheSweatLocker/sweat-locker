#!/usr/bin/env bash
# The nightly work that belongs to NO SINGLE SPORT.
#
# WHY THIS EXISTS
# ---------------
# Andy 2026-09-29: "MLB daily goes offline when baseball ends probably saving in
# LLM costs" — true, and it would have taken five other sports with it.
#
# Every cross-sport nightly job lived inside mlb_grade_overnight.yml. Audited
# 2026-09-29, that workflow ran NINE things that have nothing to do with
# baseball, including the only two that are genuinely load-bearing:
#
#   resolve_ncaaf_results.py   NCAAF's ONLY daily resolver. NCAAF plays
#                              Saturday; without this, Saturday's results are
#                              not graded until the Tue 11am ncaaf_pipeline
#                              cron — a three-day lag.
#   snapshot_team_stats.py     The ONLY writer of team_stats_rolling_history,
#                              for all six sports. It is what makes the stats
#                              that matter (EPA, success rate, SP+, and now
#                              pace) testable at all, because the live matview
#                              holds one current value per team and joining
#                              that to a played game leaks
#                              (project_rolling_stats_leak_trap_929).
#
# MLB's regular season ends in days and the playoffs finish around Nov 1. On
# that day NCAAF silently loses daily grading and every sport stops accumulating
# history — right as NBA opens (10/03) and NCAAB ramps (Nov). Nothing would have
# failed. Nothing would have been red.
#
# IT HAS ALREADY HAPPENED ONCE, in miniature: snapshot_team_stats was a bare
# `python x.py` in that workflow and died at import for two days on the
# unguarded .env read. 2026-09-27 and 09-28 are permanently missing from the
# history — Saturday's NCAAF and Sunday's NFL.
#
# WHY A SHELL SCRIPT AND NOT DUPLICATED YAML
# ------------------------------------------
# Because today alone produced two bugs from the same cause: one window written
# down five times (project_read_window_five_constants_929) and one projection
# written to two field pairs. Copying thirteen steps into a second workflow
# would be the same mistake with a bigger blast radius. This file is the ONE
# definition; both callers invoke it.
#
# ORDER IS LOAD-BEARING — do not reshuffle casually:
#   resolvers  ->  matview refreshes  ->  snapshots  ->  graders  ->  records
# Specifically snapshot_team_stats MUST follow refresh_team_stats_rolling. Its
# whole purpose is to capture the POST-refresh values; running it first records
# yesterday's numbers under today's date, which is worse than not running.
#
# Every step goes through run_step.sh so a failure is recorded in the step
# summary and the tally rather than swallowed. The caller runs `--gate` at the
# end, so the run goes red if anything failed but every step still executes.
#
# USAGE
#   .github/scripts/nightly_cross_sport.sh
# Requires SUPABASE_URL, SUPABASE_KEY in env; CFBD_API_KEY for NCAAF results.

set -uo pipefail

RUN_STEP="${GITHUB_WORKSPACE:-.}/.github/scripts/run_step.sh"
cd "${GITHUB_WORKSPACE:-.}/mlb_pipeline" || exit 1

# Refresh a matview via its RPC, and CHECK THE STATUS.
# 2026-09-22 lesson, preserved verbatim from the steps this replaces: these
# were `curl -s ... -d '{}'` with the response discarded, and
# refresh_team_situational_records returned 42809 for six days after the 09-16
# rename because a 400 and a 204 looked identical from here.
refresh_matview() {
  local rpc="$1"
  local out; out="$(mktemp)"
  local code
  code=$(curl -s -o "$out" -w '%{http_code}' -X POST \
    "$SUPABASE_URL/rest/v1/rpc/$rpc" \
    -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" \
    -H "Content-Type: application/json" -d '{}')
  if [ "$code" != "200" ] && [ "$code" != "204" ]; then
    echo "::error::$rpc failed ($code): $(cat "$out")"
    rm -f "$out"
    return 1
  fi
  echo "  ✓ $rpc ($code)"
  rm -f "$out"
}

echo "══════════════════════════════════════════════════════════════"
echo "  NIGHTLY CROSS-SPORT  $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "══════════════════════════════════════════════════════════════"

# ── 1. RESOLVERS ────────────────────────────────────────────────────────
# Football results first, because the matview refreshes below roll them up and
# the graders after that read them.
echo ""
echo "── resolvers ──"
bash "$RUN_STEP" --label "resolve_ncaaf_results.py (nightly)" \
  python resolve_ncaaf_results.py --skip-external

# 2026-09-29 NEW: NFL had NO daily resolver. resolve_nfl_results ran only on
# nfl_pipeline's Tue/Wed crons, so Sunday games were not graded until Tuesday —
# the same three-day lag NCAAF avoided only by borrowing this workflow. Andy's
# cadence model: "NHL has to be daily like MLB, grades and resolves games and
# props overnight or in morning run", and the same applies to football results
# even though football PICKS are weekly.
bash "$RUN_STEP" --label "resolve_nfl_results.py (nightly)" \
  python resolve_nfl_results.py

bash "$RUN_STEP" --label "resolve_ladder_results.py (nightly)" \
  python resolve_ladder_results.py

# ── 2. MATVIEW REFRESHES ────────────────────────────────────────────────
# Cross-sport in one shot each (union over every wired sport), so calling once
# is enough. AFTER the resolvers so newly graded games appear by morning.
echo ""
echo "── matview refreshes ──"
# Exported so run_step.sh's child shell can see it — that way a failed refresh
# lands in the tally like every other step instead of only printing ::error::.
export -f refresh_matview
for rpc in refresh_team_recent_games \
           refresh_team_situational_records \
           refresh_team_stats_rolling; do
  bash "$RUN_STEP" --label "$rpc" bash -c "refresh_matview $rpc"
done

# ── 3. SNAPSHOTS — history that cannot be reconstructed ─────────────────
# snapshot_team_stats MUST come after refresh_team_stats_rolling above.
echo ""
echo "── snapshots ──"
bash "$RUN_STEP" --label "snapshot_team_stats.py (nightly)" \
  python snapshot_team_stats.py
bash "$RUN_STEP" --label "snapshot_game_context.py (nightly)" \
  python snapshot_game_context.py

# ── 4. GRADERS ──────────────────────────────────────────────────────────
echo ""
echo "── graders ──"
bash "$RUN_STEP" --label "grade_ledger_snapshots.py (nightly)" \
  python grade_ledger_snapshots.py
bash "$RUN_STEP" --label "grade_potd.py (nightly)" \
  python grade_potd.py
bash "$RUN_STEP" --label "grade_public_receipts.py (nightly)" \
  python grade_public_receipts.py

# ── 5. RECORDS + RECEIPTS ───────────────────────────────────────────────
echo ""
echo "── records ──"
bash "$RUN_STEP" --label "aggregate_daily_records.py (nightly)" \
  python aggregate_daily_records.py
bash "$RUN_STEP" --label "compute_surface_records.py (nightly)" \
  python compute_surface_records.py
bash "$RUN_STEP" --label "backfill_public_receipts.py (nightly)" \
  python backfill_public_receipts.py

# 2026-09-29: morning_brief moved here from mlb_grade_overnight as part of the
# clean MLB split. It is cross-sport (references all seven sports) and its
# --fix re-runs graders + aggregators, so it must come AFTER the graders and
# records above and BEFORE the audits below — that way the audits see the
# repaired state rather than the gap it just healed.
bash "$RUN_STEP" --label "morning_brief.py --fix (nightly)" \
  python "${GITHUB_WORKSPACE:-..}/docs/scripts/morning_brief.py" --fix

# ── 6. AUDITS — read-mostly detectors, last so they see final state ─────
echo ""
echo "── audits ──"
bash "$RUN_STEP" --label "audit_team_alias_gaps.py (nightly)" \
  python audit_team_alias_gaps.py
bash "$RUN_STEP" --label "audit_data_quality.py (nightly)" \
  python audit_data_quality.py
bash "$RUN_STEP" --label "reconcile_resolution.py (nightly)" \
  python reconcile_resolution.py --days 7


# 2026-09-29: the heartbeat watchdog runs HERE as well, and this is the copy
# that matters. Its own note explains it lived in mlb_grade_overnight because
# "a watchdog that only runs inside the pipeline it is watching cannot report
# that pipeline failing to start" -- correct, and it missed that
# mlb_grade_overnight is itself going away when baseball ends. A watchdog that
# dies with the season cannot report the season ending.
# It watches the sport pipelines plus this workflow; this workflow failing to
# START is covered by the external monitor, not by itself.
bash "$RUN_STEP" --label "watchdog_workflow_heartbeat.py (nightly)" \
  python watchdog_workflow_heartbeat.py
echo ""
echo "══ nightly cross-sport complete ══"
