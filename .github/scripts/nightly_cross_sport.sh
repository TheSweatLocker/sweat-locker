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

# 2026-10-08 · recover receipts whose SOURCE ROW was deleted. Measured 321
# receipts ungraded on games that had already finished; 234 of them point at
# prop_jerry_reads rows that no longer exist (sampled 40 source_ids, found 0,
# every id inside the live range 34..136969 -- so deleted after the receipt was
# written, not mis-stamped). grade_public_receipts resolves a prop receipt by
# following source_id into the source table, so a dead pointer makes it
# permanently ungradeable: it logs source_row_ungraded and moves on, forever.
#
# The receipt does not need its source row. pick_label carries the whole bet,
# and the measured stat lives in <sport>_pipeline_props. Runs AFTER
# grade_public_receipts so it only sees what that pass could not resolve.
# Recovered 50 on the first run; the other 183 have no graded prop row either
# and are left alone rather than guessed.
#
# CORRECTED 2026-10-08: the orphaning is NOT ongoing. Both pruners
# (cleanup_stale_coverage_props.py, dedup_prop_dupes.py) grew a receipt pin on
# 2026-10-06 and it is verified holding -- of 411 post-guard receipts, 69 had
# their source pruned and ALL 69 already carried a result (68 NO_ACTION, 1
# Win); orphaned-and-still-ungraded post-guard is ZERO. The pin protects a
# receipt only while result IS NULL, by design. So this step is historical
# recovery, not a workaround for a live bug.
bash "$RUN_STEP" --label "grade_orphaned_receipts.py (nightly)" \
  python grade_orphaned_receipts.py --days 45 --apply

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

# == 2026-10-31 . THE RECOMPOSE SHORTFALL (R115) ==
# refresh_card_grades re-resolves the picks that are IN the blob. It cannot see
# a pick the blob DROPPED: a recompose replaces a pick in its slot and
# jerry_cache keeps only the final composition, while public_receipts keeps
# every pick that was ever live. On 10-30 that rendered a 5-0 night as 2-0, and
# the same shortfall sat on 26 of the last 30 cards -- 34-25 (57.6%, n=59) on
# the home screen against 74-60-1 (54.8%, n=135) in the receipts.
#
# The two steps are COMPLEMENTS and must both run, in this order:
#   refresh_card_grades   settles Pending picks that ARE in the blob
#   reconcile_...         restores picks that are NOT, and corrects any grade
#                         the blob has wrong against that pick's own receipt
#                         (it found 'Cleveland Guardians ML' displayed as a
#                         Loss where its receipt says Win)
#
# Runs AFTER refresh_card_grades, which reports "written" off the status code
# without a readback (R116) -- this one confirms by READBACK. Without this step
# the next recompose re-creates the shortfall and the home screen regresses by
# morning, which is why it is wired here and not left to a hand run.
bash "$RUN_STEP" --label "reconcile_card_recap_to_receipts.py (recompose shortfall)"   python reconcile_card_recap_to_receipts.py --days 35 --apply || true

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

# 2026-10-10 · JOIN SOR/SOS ONTO THE GAME ROWS. Andy: "The engine should know
# situations whrre SOR matters." It could not: compute_margin_strength above
# writes SOR/SOS per TEAM into team_computed_stats, but
# ncaaf_game_context.home_sor/away_sor were NULL on 494 of 494 rows because
# nothing in the repo ever wrote them, and 0 of 837 signal_sources rows
# mention sor or sos. SOR was a display-only number the model never saw.
# Runs immediately after the per-team fit so the join reads today's values.
#
# Writes PRE-GAME ROWS ONLY (game_date >= today) and has no --all-dates flag:
# team_computed_stats is a current snapshot, so backfilling a played game
# would stamp post-game info on a pre-game row and poison every future
# backtest reading these columns (project_rolling_stats_leak_trap_929).
for _sp in NCAAF NFL; do
  bash "$RUN_STEP" --label "populate_game_sor.py ($_sp)" \
    python populate_game_sor.py --sport "$_sp" || true
done

# 2026-10-22 - GRADE THE RANKING THAT WAS JUST WRITTEN. Andy asked for a spot
# check of SOR/SOS across every live sport; it came back clean on all six, and
# this keeps it that way without anyone re-deriving it by hand.
#
# Runs HERE, immediately after the fit and the join above, so it reads the
# values this very run produced rather than yesterday's.
#
# It checks three separate things, because the first two can both pass while
# the number is still wrong:
#   1. the ranking inside ONE source snapshot - contiguous 1..N, no duplicate
#      ranks, no team holding two rows (a surviving stale row shifts every
#      rank below it), one season, one refresh vintage, and rank order
#      consistent with value ALLOWING ties in the rounded stored value;
#   2. schedule completeness - a ranking can be perfectly contiguous and
#      still be fit on a partial schedule, which is exactly how a deleted
#      opponent put Miami (OH) at SOS rank 2 of 137 with nothing to flag it
#      until Andy eyeballed it;
#   3. whether UPCOMING game rows still agree with the source. Past rows are
#      never compared - they hold the as-of-kickoff snapshot on purpose, so
#      comparing them to today is comparing two different weeks.
#
# --warn-only: a ranking problem must not abort the rest of the nightly, and
# nothing downstream reads this step's output.
bash "$RUN_STEP" --label "verify_sor_sos_integrity.py (all sports)" \
  python verify_sor_sos_integrity.py --warn-only || true

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

  # 2026-10-06 · ESPN volumetric fallback, runs AFTER CFBD and fills only the
  # columns CFBD left NULL. CFBD's monthly quota is exhausted until Nov 1, so
  # the pull above currently returns nothing and the 10-10/10-11 slate would
  # be the first weekend with no per-game stats at all.
  #
  # Deliberately placed after, and NOT conditional on the pull succeeding:
  # when CFBD has quota it stays the primary and this writes nothing (verified
  # — a dry run against the fully-populated 10-03 slate had 0 rows to write).
  # When CFBD is dead this is the only source.
  #
  # Accuracy measured against CFBD before wiring, four weekends:
  #   09-12  160/160 names  98.02%      09-26  130/130 names  99.37%
  #   09-19  142/142 names  97.55%      10-03  108/108 names  98.50%
  # 540/540 team names resolved, no outlier week. The single biggest
  # disagreement is Colorado State / Oregon State on 10-03, where ESPN's
  # possession times sum to exactly 3600s and CFBD's to 3224s — i.e. CFBD is
  # the wrong one there.
  #
  # It does NOT backfill EPA/PPA, success rate or explosiveness. Those come
  # from CFBD play-by-play and nothing here recovers them.
  bash "$RUN_STEP" --label "ncaaf_team_game_stats_espn.py (CFBD-free volumetric)" \
    python ncaaf_team_game_stats_espn.py --days 10 --apply
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

# 2026-10-19 · POINTS SCORED, so the Team Stats matchup view has a points row.
# Neither nfl_team_stats nor ncaaf_team_stats carries a points column at all —
# only def_ppg on the defence side — so the card could show what a football
# team CONCEDES and not what it SCORES. B68 called the asymmetry "probably
# just an omission"; it blocked the one pair Andy asked for by name.
#
# DAILY, not weekly like the NCAAF volumetric recompute above, because the
# numerator comes from <sport>_game_results rather than a weekly provider
# pull: the moment Sunday's scores land, points/G is stale until this runs.
# It is the same idempotent upsert into team_computed_stats, and it REFUSES
# to write when its own points_allowed disagrees with the upstream def_ppg by
# more than 1.5 pts/g — a disagreement means the two sides of the pair came
# from different game sets, which is a finding, not a row to publish.
bash "$RUN_STEP" --label "compute_points_per_game.py (NFL + NCAAF points for/against)" \
  python compute_points_per_game.py

# 2026-10-20 · THE CALIBRATION BANNER MAINTAINS ITSELF NOW.
# ANDY: "its annoying having to update that everyday becaue i walwyas have to
# tell you can we make ti where it updates uatomaticlaly or at what point is
# it pulled and consider callibrated."
#
# It was hand-written and had drifted into quoting 139 picks (79-60) while the
# published NHL record was 67-51 (118) - and into calling 56.8% "ahead of
# break-even" when the price-aware return is -2.8%. A hand-maintained banner
# always drifts toward the flattering version, because nobody re-reads it on a
# day the number got worse.
#
# MUST run AFTER compute_surface_records so it reads today's records rather
# than yesterday's. It pulls the banner by itself once a sport clears 250
# graded reads AND 45 days live, so there is a defined end to it.
bash "$RUN_STEP" --label "refresh_calibration_notes.py (auto NHL/UFC banners)" \
  python refresh_calibration_notes.py

# 2026-10-20 · THE MODEL PANEL.
# ANDY: "I want eavh model to wrok as an indepenedtent opinion ... As if it
# was a panel of broadcaster with opinions based on real data".
#
# Refits every voice's slope and home-field intercept from history on each
# run and publishes a per-game opinion for the upcoming slate. It REFITS
# rather than carrying constants on purpose: a hardcoded slope is a number
# nobody re-checks, which is how the calibration banner drifted into quoting
# a record that did not exist.
#
# Each voice travels with its own corr/MAE/n, and a voice whose |corr| falls
# under 0.10 is published FLAGGED WEAK rather than dropped - it fits to a
# constant (NCAAF EXPLOSIVE is +15.1 on every game) so it is not a seat on
# the panel, but Andy asked that models not be removed.
bash "$RUN_STEP" --label "compute_model_voices.py (independent model panel)" \
  python compute_model_voices.py

# 2026-10-20 · EVERY MODEL GRADED PER MARKET, AND WEIGHTED ON ITS RECORD.
# ANDY: "i want to keep allmodel records and have engine weigh them
# approatley by how they perfom if one model is great at getting spreads or
# totals rigth it shoudl be wieghed accordingly vice a mdoel that ahs a bad
# track record".
#
# ensemble_scorer already weights every signal by its hit rate above
# breakeven, per sport, per market_scope - the MODELS were simply not in
# signal_registry except on junk samples (jerry_pred_spread n=8,
# mc_strong_home_ml n=6, nba_elo_edge_home_spread n=1). This grades each
# model's own number as a real bet on ml / rl / total and writes an earned
# weight, with a 30-pick floor so no weight is ever set off n=6.
#
# Runs after compute_model_voices so the panel and its grades refresh
# together.
bash "$RUN_STEP" --label "grade_model_panel.py (per-market model weights)" \
  python grade_model_panel.py

# 2026-10-20 · MONEY FLOW WAS THE ONE SIGNAL FAMILY CARRYING NO WEIGHT.
# Andy asked for the engine to weigh "all of these ad external records, with
# modney flow data, ATS sitatyionals, cohrots". Models, externals, situational,
# cohort and team_form were already in signal_registry; money flow was not,
# and it is the most dangerous family to leave unweighted because its DIRECTION
# FLIPS BY SPORT (MLB fades the public on spreads, MLB FOLLOWS the money on
# totals, NCAAF fades on spreads, and nothing works on any moneyline). So this
# grades both sides per sport/market/bucket and registers whichever one the
# data chose.
#
# Scheduled on the first night it existed rather than run by hand, because
# "works by hand, in no cron" has now been found eleven times in this repo -
# five of them today, including the NCAAF weather pull that wrote nothing in
# CI for five weeks (B107).
#
# Detector: a grading pass that finds no qualifying bucket is a legitimate
# outcome, not a failure, so it must not fail the nightly.
bash "$RUN_STEP" --detector --label "grade_money_flow.py (money-flow weights)" \
  python grade_money_flow.py

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

# ══ 2026-10-14 · MARKET SANITY, EVERY SPORT, EVERY NIGHT ══
# A value that is not a line at all keeps appearing in line columns, from a
# writer that has not been identified:
#   nhl close_puckline  held MONEYLINE PRICES on 1,120 historical rows and
#                       0.0 (an even-split median) on 3 more
#   nfl_game_context    held close_total 96.5 / close_spread -21.5 on
#                       CHI @ CAR, which finished 59-37 -- the final score
#   mlb_game_results    held close_spread 4.5..15.5 on 10 rows in an April
#                       window, 8 of them with a spread_result GRADED off it
#
# Static search found no script that derives a line from a score, so the cause
# is still open. This detects the CONDITION, which works either way, and
# repairs a context row from the results row for the same fixture. --repair
# never writes results from context (results is the graded source of record)
# and --null-unfixable only clears a results value when nothing cleaner
# exists, per backfill_game_grades: "a wrong grade is worse than a missing one
# because it looks like evidence."
#
# NOT --strict: a pre-existing implausible row must not start failing the
# nightly. Flip to --strict once the backlog is clean (3 NFL preseason rows
# with no results row to repair from, and one opener frozen by
# freeze_opening_lines pending 20261014a).
bash "$RUN_STEP" --detector --label "watchdog_market_sanity.py (nightly)" \
  python watchdog_market_sanity.py --repair


# 2026-09-29: the heartbeat watchdog runs HERE as well, and this is the copy
# that matters. Its own note explains it lived in mlb_grade_overnight because
# "a watchdog that only runs inside the pipeline it is watching cannot report
# that pipeline failing to start" -- correct, and it missed that
# mlb_grade_overnight is itself going away when baseball ends. A watchdog that
# dies with the season cannot report the season ending.
# It watches the sport pipelines plus this workflow; this workflow failing to
# START is covered by the external monitor, not by itself.
# == 2026-10-14 - THE PANEL LAYER - BUILD AND MONITOR ==
# ANDY: "we wont know until we build and monitor surely its better than what we
# are doing." These run nightly so the record-weighted engine accumulates a
# FORWARD record instead of waiting for someone to run it by hand.
#
# Order matters: weights derive from records, and the shadow engine reads the
# weights.
#
# All are DETECTORS - they must not fail the nightly. Until migration
# 20261014c is applied, panelist_weights and engine_shadow_picks 404, so these
# report that and write nothing. The shadow engine still blocks duplicates via
# panelist_registry in that state, so its decisions stay honest meanwhile.
#
# Nothing here publishes a pick. engine_shadow_picks exists so the weighted
# panel can be compared head to head with what actually shipped, which is the
# only basis on which it should ever be promoted.
bash "$RUN_STEP" --detector --label "voter_rolling_form.py --today" \
  python voter_rolling_form.py --today
bash "$RUN_STEP" --detector --label "voter_pace_efficiency.py --today" \
  python voter_pace_efficiency.py --today
bash "$RUN_STEP" --detector --label "voter_goalie_special.py --today" \
  python voter_goalie_special.py --today
bash "$RUN_STEP" --detector --label "compute_panelist_weights.py --apply" \
  python compute_panelist_weights.py --apply
bash "$RUN_STEP" --detector --label "shadow_panelist_engine.py --today" \
  python shadow_panelist_engine.py --today

# And the guard that stops duplicate votes coming back. This class has been
# fixed by hand five times (09-20, 09-26, 09-29 twice, 10-14) and each time the
# NEXT one still got through, so it is a nightly check now rather than a thing
# someone happens to notice.
# 2026-10-14: and the pre-lock readiness report. A lock freezes whatever is
# on the row, so coverage/scale/freshness are checked INSIDE the lock window
# rather than across all rows -- coverage on "all rows" is exactly what hid
# NCAAF epa at ratio 0.10 and NFL v4 at 0.02. Exits 1 on a problem, so it is
# a detector and reports without failing the nightly.
# 2026-10-16: void picks whose game never resolved. A postponed game that
# comes back under a NEW game_id (MLB ids are date-dependent) orphans its
# POTD and props as Pending forever — the 2026-07-21 BAL@BOS POTD sat that
# way for 87 days. Threshold is 4 days, so a one-night delay is untouched
# and still grades normally. Detector: it must not fail the nightly.
bash "$RUN_STEP" --detector --label "void_orphaned_picks.py (nightly)" \
  python void_orphaned_picks.py --apply

bash "$RUN_STEP" --detector --label "prelock_readiness.py (nightly)" \
  python prelock_readiness.py

bash "$RUN_STEP" --detector --label "audit_duplicate_votes.py (nightly)" \
  python audit_duplicate_votes.py


# 2026-10-20 · THE CHECK THAT WOULD HAVE CAUGHT B107 FIVE WEEKS EARLIER.
# NCAAF weather was dark in CI from 09-26 to 10-20 -- 171 games -- and every
# existing signal said the pipeline was healthy: heartbeat logged start AND
# end on every cron slot, and the step was green because it carried
# continue-on-error and returned before doing any work. Nothing anywhere
# asked whether a column that should be full was EMPTY.
#
# DETECTOR here, deliberately. This runs at 6:30am ET, before the daily sports
# have built the night's slate, so "no rows" for MLB/NBA/NHL is the expected
# answer at this hour and must not fail the nightly. The HARD version lives at
# the end of each sport's own pipeline, right after the steps that write --
# which is where a dark column is actionable.
bash "$RUN_STEP" --detector --label "assert_slate_freshness.py (nightly)" \
  python assert_slate_freshness.py

bash "$RUN_STEP" --label "watchdog_workflow_heartbeat.py (nightly)" \
  python watchdog_workflow_heartbeat.py

# 2026-10-23 · B152. Every dead column reference is a request that CANNOT
# succeed, and until today they were all invisible: call sites swallowed the
# 400 with `if r.status_code != 200: return []`, so the step exited green with
# no data. That is how nfl_player_stats.anytime_td shipped hundreds of
# guaranteed-fail requests per run, and how ATD ended up computing from an
# empty list (its conviction came out INVERTED as a result).
#
# One OpenAPI request describes all 217 tables, so this whole audit is cheap —
# which matters, because it was request volume on a micro instance that took
# the app down for paying users today.
#
# --detector: report, never fail the nightly. A dead column is a code bug to
# fix in daylight, not a reason to abort the night's slate.
bash "$RUN_STEP" --detector --label "schema_guard.py --audit" \
  python schema_guard.py --audit

# B157 · THE UNRUN-WRITER DETECTOR (2026-10-23)
# Our recurring failure mode is not missing code, it is code that exists and
# never runs. Eight instances found by accident in three days, each one
# surfaced because Andy opened a screen and saw a wrong number: a released QB
# still starting, 1,469 NCAAF stat rows withheld, the Sharp breakdown frozen a
# month in front of users. bridge_football_game_ids.py is the sharpest case --
# it diagnoses the NFL context/results id split, measured "0 of 316" on
# 2026-09-21, and has never run, so the bug it fixes is still open.
# Same posture as schema_guard above: report, never fail the night.
bash "$RUN_STEP" --detector --label "audit_unscheduled_writers.py"   python audit_unscheduled_writers.py

# sweat_rating was 19.3 DAYS stale (2026-10-23)
# compute_sweat_rating.py was in no workflow, so its 139 NCAAF rows aged past
# matchup_story's 4-day freshness gate and were withheld from every read.
# Internal only -- project_sweat_rating_surfacing_pinned_1004 still stands;
# this refreshes the number without surfacing it.
bash "$RUN_STEP" --label "compute_sweat_rating.py"   python compute_sweat_rating.py --all --write

# ══ 2026-10-23 · B168 · price_picks HUNG, WHICH IS WHY PRICING DECAYED ══
# It never completed: run against one sport it exited 124 on a 115s timeout
# with zero output. jerry_reads pricing had fallen from 100% in early October
# to 55% on 10-23, 21% on the 10-24 slate and 0% on 10-27/28/31, and an
# unpriced pick cannot be graded for ROI or CLV.
#
# quote_index paged ALL of line_history for each sport with a 14-day floor:
# NFL 702,512 rows, NBA 639,202, and for NCAAF the COUNT ITSELF timed out.
# At 1,000 a request that is 700+ requests per sport with offsets reaching
# 700,000, every deep offset walking the rows it skips — the same shape that
# took the app down on 10-21. So it being unscheduled was PROTECTIVE, and
# scheduling it as written would have been dangerous.
#
# It now reads ONE ordered page of the most recent captures per sport, which
# is all the current board requires: 6 requests and 8.6 seconds for every
# sport, against a hard timeout before. That is why it is safe to schedule at
# last. Pricing on the 10-24 slate went 21% -> 53% and 10-31 went 0% -> 87%
# on the first real run.
#
# The rest of the gap is NOT this script: 93 of 107 misses are "no quotes for
# this game", the jerry_reads/line_history game_id mismatch tracked as B160.
bash "$RUN_STEP" --label "price_picks.py"   python price_picks.py --write

# ══ 2026-10-29 · THE ALL-SPORTS REFIT WAS ABOUT TO DIE WITH BASEBALL ══
# refit_signal_registry is what turns signal_attribution into the hit_rate /
# sample_n / baseline_pct that edge_weight reads. Without a registry row a
# signal falls to UNMEASURED_FLOOR (0.20) and votes on a guess.
#
# Its only all-sports invocation lived in mlb_pipeline.yml. nba_pipeline and
# nhl_pipeline each run it with --sport for themselves, so when MLB goes
# offline — the regular season ends in DAYS — NFL and NCAAF lose signal
# measurement entirely and nothing goes red. That is precisely the failure
# this workflow was created on 09-29 to prevent, for the same reason:
# "it would have taken five other sports with it".
#
# Measured on 10-29 before this was added: 430 enabled GAME signals across
# five sports, 127 measured (29.5%). NFL was 0 of 95 while NCAAF was 79 of
# 95, because NCAAF rides the untargeted run and NFL's numbers were computed
# and never written. Running it here took the total to 179 (41.6%).
#
# --min-n 10 matches the two sport pipelines so a signal cannot be tiered on
# one window here and a different one there.
# 2026-10-29 . signal_attribution.result is the PICK's outcome stamped onto
# every signal row that fired on it. signal_was_right grades signal_side
# against the stored margin and close line instead, and v_signal_records --
# the game card tooltip -- now counts THAT column. signal_attribution_grade.py
# writes it at grade time; this is the safety net for rows graded before that
# wiring existed, and for any row where the numbers landed late
# (feedback_late_data_must_invalidate_derived_output). --only-null keeps it
# cheap once it has caught up.
bash "$RUN_STEP" --label "backfill_signal_was_right.py" \
  python backfill_signal_was_right.py --apply --only-null

bash "$RUN_STEP" --label "refit_signal_registry.py (all sports)" \
  python refit_signal_registry.py --days 90 --min-n 10

# ══ 2026-10-29 · NOTHING CHECKED THAT A READ AGREES WITH ITS OWN PICK ══
# Andy: "i need verification that each Jerry game read write up matches the
# picks and makes sense on nfl and ncaaf, nba, and nhl."
#
# Three things can disagree on one card and no step compared them:
#   engine   <sport>_game_context.primary_play
#   badge    jerry_reads.call_market/side/line
#   write-up jerry_reads.short_read/long_read
# They are written by different steps, so they drift independently. First
# run, +14d: NFL 9, NCAAF 21, NBA 4, NHL 0 — including a badge reading
# 'Texas Tech ML' above prose saying "This PASS on the moneyline ... too
# thin to clear conviction thresholds".
#
# --warn-only ON PURPOSE, for now. There is a real backlog (14 findings a
# reader would see), and a step that is red from the day it ships is a step
# that gets muted (feedback_a_detector_must_know_the_intentional_case). It
# reports into the log and the heartbeat; drop --warn-only to make it
# blocking once the backlog is cleared, which needs the read generators to
# re-run (they need a live ANTHROPIC_API_KEY, so CI, not local).
bash "$RUN_STEP" --label "audit_read_pick_consistency.py" \
  python audit_read_pick_consistency.py --days 14 --warn-only

# ══ 2026-10-29 · THE RECEIPT DRIFTED FROM THE BOARD BEFORE KICKOFF ══════
# Andy: "We need to fix and make sure this is not an issue across other
# games and sports (all games)." Measured that hour across every ungraded
# game_read receipt for today forward: 13 of 31 (42%) named a pick we no
# longer publish — NFL 11, NBA 1, NHL 1. 'LV +1.5' vs engine 'NYJ ML',
# 'JAX -4.5' vs 'JAX -9.5', 'DET -4.5' vs 'DET ML'.
#
# A receipt is frozen on purpose (pick_side/line/label immutable by trigger
# 20260918b) while the engine keeps re-deriving the pick as lines move, so
# the longer a game sits the further the record drifts. That is also how
# project_receipts_grade_the_opposite_side_1028 happened: 179 receipts
# grading a side we had abandoned.
#
# --apply is deliberate. The guards are what make it safe, and they refuse
# rather than guess: the game must be PROVABLY un-started from its own
# start column, the receipt must be UNGRADED, and MLB is skipped outright
# for having no start-time column. A receipt for a game already under way
# is left exactly as published, because that is the honest record.
#
# Runs AFTER the read audit so the log shows the picks first, then the
# record being brought in line with them.
bash "$RUN_STEP" --label "resync_pregame_receipts.py" \
  python resync_pregame_receipts.py --apply

# ══ 2026-10-29 · THE DEGEN ONLY EXISTED WHEN BASEBALL DID ════════════════
# Andy: "no daily degen is unsat, that should be populated everyday,
# shouldn't be as selective as the ladder."
#
# Both writers live ONLY in mlb_pipeline.yml (lines 1120 and 1161), so the
# surface dies with the MLB season — the same trap that would have taken
# NFL/NCAAF signal measurement offline, fixed in this same file today.
#
# And it was already patchy: jerry_cache, which is what the app reads, holds
# only 8 daily_degen keys — 10-29, 10-24, 10-23, 10-17, 10-16, 10-15, 10-10,
# 10-09. Everything from 10-25 to 10-28, 10-18 to 10-22 and 10-11 to 10-14 is
# simply absent. The `daily_degen` TABLE disagrees with the cache (it has
# 10-27 and 10-28, which the cache does not), so the two surfaces were never
# in step either.
#
# The anchor is the only writer of the cache and it returned None whenever
# fewer than 2 legs cleared conviction 70. Two fixes shipped with this:
# the verdict allowlist (football writes a TIER into call_verdict, so a
# `= BACK` filter saw 0 of 50 rows) and a stepped floor down to 55. Running
# it here makes it daily and sport-agnostic.
bash "$RUN_STEP" --label "jerry_anchor_daily_degen.py" \
  python jerry_anchor_daily_degen.py

# The resync drops the old price on purpose — carrying an ML price onto a
# spread replacement is the 10-28 "SAS -13.5 shown at -650" defect. So its
# rows land UNPRICED and have to be priced in the same run that created
# them: an unpriced receipt cannot be ROI'd or graded for CLV, and "we will
# catch it next time" is how POTD price coverage fell 82% -> 32% (R30).
#
# It must be backfill_receipt_prices, NOT price_picks. Measured right after
# wiring this: price_picks reported "wrote 12/12" and only 1 of the 13
# replacement RECEIPTS carried a price, because price_picks prices
# jerry_reads. Two tools, two tables, and the step name made it look done.
bash "$RUN_STEP" --label "backfill_receipt_prices.py (after receipt resync)" \
  python backfill_receipt_prices.py --apply
echo ""
echo "══ nightly cross-sport complete ══"
