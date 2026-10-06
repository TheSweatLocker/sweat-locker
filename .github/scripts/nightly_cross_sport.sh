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

# 2026-10-06: CFBD monthly quota exhausted. resolve_ncaaf_results takes its
# scores from CFBD, so on a dead quota it grades NOTHING ("CFBD games w/
# scores: 0"). ESPN needs no key and no quota and agreed with CFBD on 53
# of 53 scores for 2026-10-03, 0 mismatches. Runs AFTER the CFBD attempt
# and only fills home_score IS NULL rows, so CFBD stays primary.
bash "$RUN_STEP" --label "ncaaf_results_espn.py (CFBD-free fallback)" \
  python ncaaf_results_espn.py --days 10 --apply

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

# ══ 2026-10-04 · RE-RESOLVE PAST CARDS ══
# jerry_cache.sweat_card_{date} stores top_8 results and a top_8_summary rollup
# FROZEN AT BUILD TIME, and nothing ever went back to fix a pick that resolved
# after its own card was built. 10-03 sat at "3-2 (2 pending)" on the home
# screen while both pending props had WON.
#
# It is not one day: RecapStrip builds BOTH the yesterday figure and the
# "CARD L30D" rollup by summing top_8_summary across the last 30 card rows, so
# every late-resolving pick was missing from the 30-day number too. Found 7
# such picks across 4 cards going back to 09-01.
#
# Runs AFTER grading so it sees the night's fresh results.
bash "$RUN_STEP" --label "refresh_card_grades.py (re-resolve past cards)"   python refresh_card_grades.py --days 35 --apply || true

# ══ 2026-10-04 · MARGIN-BASED SOS/SOR + THE SWEAT RATING ══
# compute_schedule_strength above rates on WIN PERCENTAGE only. These rate on
# opponent-adjusted MARGIN, which beat it out of sample on all six sports
# (NCAAF +0.575 -> +0.675, NFL +0.344 -> +0.393, NCAAB +0.401 -> +0.458).
# Both write alongside the win% keys rather than replacing them, so the two can
# be compared on live data before anything is retired.
#
# SCHEDULED HERE DELIBERATELY. compute_schedule_strength was run once by hand
# on 09-26 and never wired up, so every value froze that day and decayed
# silently for four days. A rating nobody refreshes is worse than no rating,
# because the staleness is invisible.
#
# Order matters: the Sweat Rating consumes the margin ratings, so it runs
# second. NCAAF publishes FBS only (SP+ coverage is the division line) while
# still FITTING on every game, including FCS opponents.
for _sp in NFL NCAAF NHL NBA NCAAB MLB; do
  bash "$RUN_STEP" --label "compute_margin_strength.py ($_sp)" \
    python compute_margin_strength.py --sport "$_sp" --write || true
done
bash "$RUN_STEP" --label "compute_sweat_rating.py (all sports)" \
  python compute_sweat_rating.py --all --write || true

# 2026-10-03 · EXTERNAL SOR BENCHMARK. Andy: "add as weekly benchmark for
# data." Records strengthofrecord.com's published FBS Strength of Record
# beside ours. A reference point ONLY -- never an input to a pick, and
# deliberately written to its own sor_benchmark table rather than
# team_computed_stats, because that table is UNIONed into
# team_stats_rolling and GameDetailV2 selects '*' from it, so a stat_key
# landing there reaches the app card on its own.
#
# Not adopted as our number: it carries no SOS at all, it is CFBD-derived
# like ours, and its SOR correlates +0.970 with raw win% against our
# +0.911 -- closer to being the record than ours, so less independent
# schedule signal, not more. Kept because our SOS/SOR has no external
# check of any kind: CFBD's own `sos` field is null on 0 of 808 rows
# (2021-2026), and a deleted opponent inflated Miami (OH) to SOS rank 2 of
# 137 with nothing to flag it until Andy eyeballed it.
#
# Runs AFTER compute_schedule_strength so it grades the fresh numbers.
# Upserts on (sport, season, week, source), so a daily run refreshes the
# live week's row rather than accumulating duplicates. Exits 0 when the
# table is absent or the external API is down -- a benchmark must never
# fail the nightly.
bash "$RUN_STEP" --label "benchmark_external_sor.py (external SOR reference)" \
  python benchmark_external_sor.py

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
# 2026-10-03 · ELEVENTH INSTANCE, and this one removed our only way to CHECK
# the others. ncaaf_team_game_stats_pull.py was written 09-30 and never
# scheduled: last hand-run 10-01 00:29, so every week-5 game was absent and
# the per-game log — the single source that can verify the season aggregates
# published below — was itself the least trustworthy table we had.
#
# Andy: "first and foremost we need to ensure all data is accurate for teams."
# Scheduling the pull is half of that. The other half is actually comparing
# the two sources, which nothing did (project_stat_integrity_audit_1002,
# "no verification exists").
#
# Runs the FULL current season, not just the latest week, deliberately: the
# pull is idempotent on (cfbd_game_id, team) and a whole-season re-run
# self-heals gaps. That matters because the log had holes, not just lag —
# Northwestern held weeks 1, 3, 4 and no week 2 — and a missing row reads
# exactly like a bye. ~17 weeks x 3 CFBD endpoints a night.
# == 2026-10-06 - WEEKLY, NOT NIGHTLY (Andy call) ==
# CFBD returned "Monthly call quota exceeded" on 2026-10-06 - a month of
# quota gone by the 6th. This step was the main consumer: ~17 weeks x 3
# endpoints every night, and on a dead quota it retried each 429 three
# times, so ~51 doomed calls a night. It runs in BOTH mlb_grade_overnight
# and nightly_cross_sport, so double it.
#
# Andy: "I dont think CFB should be a daily run, it should only run once a
# week to pull the stats, probably best it runs on a sunday after all games
# played for week and one run updates all stats."
#
# The data agrees: NCAAF plays Saturday plus a few Thu/Fri games, so nothing
# changes Mon-Fri. Sunday ET once is correct - and it PRESERVES the
# deliberate full-season re-run documented above (--all-weeks), because at
# 51 calls a WEEK instead of a night that is affordable: ~204/month vs
# ~1,530. The script default is now a 3-week trailing window for ad-hoc
# runs; --all-weeks here keeps the weekly pass self-healing the holes that
# made the per-game log untrustworthy in the first place.
if [ "$(date -u +%u)" = "7" ] || [ "${FORCE_WEEKLY_STATS:-0}" = "1" ]; then
  bash "$RUN_STEP" --label "ncaaf_team_game_stats_pull.py (weekly, full season)" \
    python ncaaf_team_game_stats_pull.py --all-weeks
else
  echo "  (skip ncaaf_team_game_stats_pull - weekly, Sundays only; FORCE_WEEKLY_STATS=1 overrides)"
fi

# Compares ncaaf_team_stats (cumulative, what we publish) against
# ncaaf_team_game_stats (immutable per-game, what can check it) and FAILS the
# step on a game-count gap, a week hole on a team also short a game, or a
# wholly-empty column. Writes nothing — when the two disagree, which one is
# stale is a judgement call, but it has to be a visible one.
#
# Caught on its first run: penalties_yards populated 0 of 1776 rows, because
# CFBD sends totalPenaltiesYards as "3-35" (count-yards) and the pull read it
# with _int(). A 100% parse failure looked identical to CFBD not sending the
# field. penalty_yds_pg is published for 266 teams WITH a national rank and
# was cited as a pick reason on the 10-03 FAU card. Now 672/672 on played
# games, and pass/rush/turnover drift is 0 across all 139 rated teams.
#
# Must run AFTER the pull above so it grades the fresh log.
# --report-only: this is a stats-freshness REPORT and it runs inside the
# overnight GRADER. A stale NCAAF aggregate is not a grading failure, and a red
# X on the grader for a stats lag is exactly how a real grading failure gets
# ignored later. The verdict still prints in full; only the exit code is
# suppressed. Run it without the flag to gate on it.
# Same weekly cadence as the pull it verifies - comparing two tables
# that neither changed since yesterday is waste and a noisy detector.
if [ "$(date -u +%u)" = "7" ] || [ "${FORCE_WEEKLY_STATS:-0}" = "1" ]; then
  bash "$RUN_STEP" --label "reconcile_ncaaf_stat_sources.py (aggregate vs per-game)" \
  python reconcile_ncaaf_stat_sources.py --report-only

  bash "$RUN_STEP" --label "recompute_ncaaf_per_game_stats.py (volumetric)" \
  python recompute_ncaaf_per_game_stats.py
else
  echo "  (skip NCAAF reconcile/recompute - weekly, Sundays only)"
fi

# 2026-10-03 · DAILY FLOOR for pace (plays_pg, top_min_pg -> NCAAF + NFL).
# collect_pace_stats.py IS scheduled, inside ncaaf_pipeline.yml and
# nfl_pipeline.yml, but gated on `mode == full || resolver_only`. The NCAAF
# mode map sends Fri 18:00 and Sat 12:00 to card_only, so pace is skipped on
# both football days, and mode is derived from `date -u +%H` at RUN time while
# this repo's crons routinely land 4-7h late -- so a delayed trigger silently
# lands in a different bucket than the one it was scheduled for.
# cross_sport_lines.yml documents that same run-time-vs-trigger confusion.
#
# No --sport, so one call covers every sport the script knows (NCAAF, NFL).
# Idempotent upsert, and now that refreshed_at is stamped explicitly a repeat
# run is visible rather than silent. Same "daily floor, not the ideal" shape as
# price_picks.py below: the in-pipeline calls stay, because running right after
# each sport's stats pull keeps the numerator and denominator together.
bash "$RUN_STEP" --label "collect_pace_stats.py (pace floor, all sports)" \
  python collect_pace_stats.py
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

# 2026-10-02 · GATES MUST BE RE-APPLIED AFTER THE BUILD, NOT ONLY DURING IT.
# Every context builder calls apply_all_defensive_gates and the gates are
# correct -- verified on the stored NE @ BUF row (BUF ML -305 => BUF -7,
# COVERAGE). But the live row still read "BUF ML LEAN -305" after a full
# rebuild, because gates run on the IN-FLIGHT row and the market columns are
# not reliably populated at that moment; a price-dependent gate then finds
# nothing to act on and returns the pick untouched.
#
# Measured before this step existed, on forward games:
#     NFL    40 of 69 ML picks past -200 (incl. DET ML -225 PRIME)
#     NCAAF  13 of 29 past -200          (incl. Michigan ML -225 STRONG)
#     NHL    28 of 48 unpriced but at a published tier
# After: 0 / 0 / 0, and the pass re-runs to CHANGED 0 -- a fixed point.
#
# Writes via the two-step unlock (clear pick_locked_at, write, re-stamp) and
# READS BACK every row: enforce_pick_lock() refuses a label change on a
# stamped game and returns 204 anyway, so trusting the status code reported
# 103 patches the database had actually discarded.
bash "$RUN_STEP" --label "apply_pick_gates_post_pass.py (re-gate stored picks)" \
  python apply_pick_gates_post_pass.py --apply

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
