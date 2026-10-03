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

# ══ 2026-10-01 · NHL — THE SPORT THE 09-29 COMMENT ABOVE NAMED ══
# Read the quote three lines up: "NHL has to be daily like MLB, grades and
# resolves games and props overnight or in morning run". That commit added the
# NFL resolver and never added NHL — the sport Andy actually named.
#
# The consequence is an ORDERING INVERSION ACROSS WORKFLOWS, which is worse
# than a missing step and much harder to see. nhl_resolve_results.py existed
# and worked, but lived ONLY in nhl_pipeline.yml, whose earliest cron is
# '5 12 * * *' and which that file documents as a measured median +4.7h late —
# so it actually lands ~16:45 UTC. Every grader that consumes NHL results
# (grade_public_receipts, aggregate_daily_records, compute_surface_records) is
# BELOW this line and runs at 10:30/12:30 UTC. The grader therefore ran four to
# six hours BEFORE the resolver, every single day.
#
# Measured 2026-10-01 at 14:59 UTC, which is what exposed it: all three 9/30
# games still had NULL scores, nhl_sides read n=2 / last_pick 2026-09-29, and
# Andy saw unresolved picks in the app at 11:27am ET. Running the resolver by
# hand took nhl_sides to n=4 immediately — nothing was broken except the order.
#
# Nothing downstream needed changing; the resolver only had to run first.
bash "$RUN_STEP" --label "nhl_resolve_results.py (nightly)" \
  python nhl_resolve_results.py

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
# --backfill 7 rather than one date: a game that resolves late, or a night this
# workflow misses, then self-heals instead of leaving a permanent Pending cell
# on the Receipts calendar. Safe to repeat now that a parse failure cannot
# overwrite an existing grade (the 08-16 / 08-21 regression) — verified
# idempotent: two consecutive runs both left the record at 74-55-4.
bash "$RUN_STEP" --label "grade_potd.py (nightly)" \
  python grade_potd.py --backfill 7
# 2026-09-29 POTD VERIFICATION, ALL SPORTS. Andy: "queue up POTD verification
# for all sports and ensure Receipts tab calendar is and record up to date".
# POTD lives on three surfaces that can disagree — jerry_cache, 
# daily_best_bet_history (what the Receipts calendar renders and the published
# record counts), and the record itself. Measured 2026-09-29: 33 POTDs existed
# in jerry_cache with NO calendar row, so they were invisible to users and
# absent from the record denominator, and no single query would have said so.
# Runs AFTER grade_potd so it checks the post-grading state. Read-only; exits 1
# on anything actionable so the gate turns the run red.
bash "$RUN_STEP" --detector --label "verify_potd_coverage.py (all sports)" \
  python verify_potd_coverage.py --days 45
bash "$RUN_STEP" --label "grade_public_receipts.py (nightly)" \
  python grade_public_receipts.py

# ── 5. RECORDS + RECEIPTS ───────────────────────────────────────────────
echo ""
echo "── records ──"
bash "$RUN_STEP" --label "aggregate_daily_records.py (nightly)" \
  python aggregate_daily_records.py
# 2026-09-30 · SOS + SOR, every sport.
# compute_schedule_strength.py was written 09-26 for Andy ("SOS and SOR added
# somewhere in game detail across all sports"), run ONCE by hand that day, and
# NEVER SCHEDULED -- so the numbers froze on 09-26 and silently decayed, the
# same way split_sharp_card_record.py did. Seventh instance of this pattern.
#
# Must run AFTER the resolvers above: it reads team_recent_games, so a night
# whose results have not landed yet would recompute every team on a stale
# record and stamp it as fresh.
bash "$RUN_STEP" --label "compute_schedule_strength.py (SOS/SOR, all sports)"   python compute_schedule_strength.py

# 2026-10-02 · THE EIGHTH AND NINTH INSTANCES OF THE SAME PATTERN.
# recompute_ncaaf_per_game_stats.py and recompute_nfl_epa_units.py were both
# written 09-26, run once by hand, and never scheduled. Their output is served
# as AUTHORITATIVE: migration 20260926d makes a stat_key present in
# team_computed_stats win over the matview half, and the team_stats_rolling
# view is what matchup_story.py reads to build every Jerry read's
# `stat_matchups` fact. So a frozen row is not a gap, it is a wrong number
# published with a fresh-looking percentile beside it.
#
# Measured on the 10-02 NCAAF slate, against pass_yards/games from
# ncaaf_team_stats (the card's own arithmetic):
#     Penn State     truth 243.75   read published 287.0
#     Northwestern   truth 269.0    read published 403.5  (rank 1 of 133)
#     Pittsburgh     truth 347.25   read published 271.7
#     Delaware       truth 262.0    read published 325.7
# Wrong on 5 of 6 teams, six days and a full game-week stale. Northwestern
# was rendered the best passing offence in the country off a 2-game divisor.
#
# Must run AFTER the per-sport stats pulls (they read ncaaf_team_stats /
# nfl_team_stats cumulative totals) and after the resolvers above, for the
# same reason compute_schedule_strength does.
bash "$RUN_STEP" --label "recompute_ncaaf_per_game_stats.py (volumetric)" \
  python recompute_ncaaf_per_game_stats.py
bash "$RUN_STEP" --label "recompute_nfl_epa_units.py (off/def EPA units)" \
  python recompute_nfl_epa_units.py

# 2026-10-02 · TENTH INSTANCE. price_picks.py was run ONCE by hand on 09-24 and
# never scheduled. Measured tonight: of 160 jerry_reads for 10-02 onward, 145
# had price_american NULL — ungradeable for ROI, permanently — and the 15 that
# carried a price were priced on 09-24, eight days stale:
#
#     Penn State ML   logged -278   actual market -135
#     Virginia Tech -3 logged -205  actual market -115
#
# A win at -278 pays 0.36u; at -135 it pays 0.74u. So every ROI figure drawn
# from this column understates returns, and a stale price is also what gets
# shown to a subscriber as the number they can have.
#
# Runs AFTER the resolvers and records above, and re-prices every FORWARD game
# each night — price_picks.py only freezes a price once a game has started, so
# a repeat run cannot overwrite the historical record of what we published.
#
# NOTE this is the daily floor, not the ideal. A price belongs to a specific
# (market, side, line), so the right place is also immediately after each
# sport's read generation — when the juice-reroute moves a pick from spread to
# moneyline, the price must move with it. Wiring that per-pipeline is queued.
bash "$RUN_STEP" --label "price_picks.py (ROI prices, all sports)" \
  python price_picks.py --write

# 2026-10-02 · THE QB ON THE NFL CARD WAS A JULY SNAPSHOT.
# Andy: "accounts for injured QB1s?" It did not. nfl_game_context does not read
# nfl_starters -- it re-derives the starter from nfl_player_stats, and that
# block never re-runs. IND @ WAS carried updated_at = computed_at = 2026-07-22
# with home_qb_name = "Jayden Daniels" while nfl_starters wk4 said Marcus
# Mariota, and team_form_enriched_at = 2026-10-02 made the row look fresh.
# Only 16 of 223 forward games carried a QB name at all; 213 rows / 420 fields
# were corrected on the first run.
#
# Points the context at the resolver that got the work: ESPN's roster endpoint
# is ALPHABETICAL BY SURNAME, which had nfl_weekly_starters.py wrong on 72% of
# teams until it was rebuilt on the box-score rule (26% -> 95%).
# FORWARD ROWS ONLY -- a past game's QB is the record of who actually played.
bash "$RUN_STEP" --label "refresh_nfl_context_qb.py (forward QB1s)" \
  python refresh_nfl_context_qb.py --apply

bash "$RUN_STEP" --label "compute_surface_records.py (nightly)" \
  python compute_surface_records.py
# 2026-09-30 · Sharp Card headline/breakdown reconciliation.
# split_sharp_card_record.py was written 09-24 for Andy: "the record overall
# and the props in small letters below it dont really match ... its deceiving
# to users". It writes sharp_card_sides + sharp_card_props so the headline
# equals sides + props instead of stacking three unrelated populations.
#
# It was NEVER WIRED INTO A WORKFLOW. Run once by hand on 09-24 and never
# again, while app/index.tsx:10146 reads both surfaces every session — so the
# breakdown users saw froze at its 09-24 values for six days (sides 133-106
# vs an actual 155-115-9). The fix decayed from the day it shipped.
#
# MUST run AFTER compute_surface_records: it reads the sharp_card rows that
# step writes and derives the two split surfaces from them.
bash "$RUN_STEP" --label "split_sharp_card_record.py (nightly)" \
  python split_sharp_card_record.py --write
bash "$RUN_STEP" --label "backfill_public_receipts.py (nightly)" \
  python backfill_public_receipts.py

# 2026-09-29: morning_brief moved here from mlb_grade_overnight as part of the
# clean MLB split. It is cross-sport (references all seven sports) and its
# --fix re-runs graders + aggregators, so it must come AFTER the graders and
# records above and BEFORE the audits below — that way the audits see the
# repaired state rather than the gap it just healed.
bash "$RUN_STEP" --detector --label "morning_brief.py --fix (nightly)" \
  python "${GITHUB_WORKSPACE:-..}/docs/scripts/morning_brief.py" --fix

# ── 6. AUDITS — read-mostly detectors, last so they see final state ─────
echo ""
echo "── audits ──"
bash "$RUN_STEP" --detector --label "audit_team_alias_gaps.py (nightly)" \
  python audit_team_alias_gaps.py
bash "$RUN_STEP" --detector --label "audit_data_quality.py (nightly)" \
  python audit_data_quality.py
bash "$RUN_STEP" --detector --label "reconcile_resolution.py (nightly)" \
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
