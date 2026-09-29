---
name: project_rolling_stats_leak_trap_929
description: 🚨 team_stats_rolling is CURRENT-ONLY (one refreshed_at). Joining it to past games leaks. I measured a fake NCAAF 67.8% ATS / z=+4.07 this way before catching it. snapshot_team_stats was dying in CI — lost 9/27 and 9/28.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-29T16:14:36.180Z
---

Andy 2026-09-29: "what about for live rolling stats, have we assessed that angle
for this week?"

## THE TRAP — and I walked into it

`team_stats_rolling` is a matview with exactly ONE `refreshed_at` value. There is
no history in it. So joining it to games that have already been played uses
end-of-today stats to "predict" a week-1 game — **the stat contains the game
being predicted.**

Measured this way, a net-EPA differential looked spectacular:

    NCAAF quartiles (home-covered rate): 70.4% / 64.8% / 45.1% / 38.0%
    as a lens: 172-112 (60.6%) n=284  z=+2.75
    gated |z|>=0.5: 118-56 (67.8%) n=174  **z=+4.07**

Perfectly monotonic across 284 games and the most significant number measured all
day. **It is leaked and worthless.** Monotonic quartiles are exactly what a leak
produces — teams that ENDED the period good covered more often over it. Identical
shape to [[project_prop_l5_leak_922]] (l5_hit_count=5 returned 308-4).

RETRACTED before reporting. Recorded because the next person to join
team_stats_rolling to a historical game will get the same beautiful number.

## THIS WAS ALREADY SETTLED ON 9/26 — I should have read first

`snapshot_team_stats.py`'s own docstring says it, verbatim:

> "Andy 2026-09-26, approving the conclusion of study_stat_edge: the five stats
> with leak-free history carry no ATS edge over 16,736 games, and the stats that
> might — EPA, success rate, explosiveness, SP+ — cannot be tested at all because
> team_stats_rolling is a matview holding only the current value."

So: five testable stats, no edge, n=16,736. The interesting stats are UNTESTABLE
until history accumulates. `snapshot_team_stats.py` exists to build that history.

## AND IT WAS SILENTLY BROKEN

`snapshot_team_stats.py` is one of the 28 scripts carrying the unguarded
`open(.env)` fixed today — and it is wired into `mlb_grade_overnight` as a bare
`python snapshot_team_stats.py`, NOT through run_step.sh, so its failures were
never logged to workflow_heartbeat. Zero step_failed events ever named it.

Proof it was broken, from timing:
  · .env guard landed 15:04 UTC
  · mlb_grade_overnight ran 13:13, 15:00, and 16:05 UTC
  · team_stats_rolling_history snapshot_dates are ONLY {2026-09-26, 2026-09-29}
  · today's 4,694 rows came from the 16:05 run — the only one after the fix

**9/27 and 9/28 are missing** — Saturday's NCAAF and Sunday's NFL. The two
snapshots that would have captured post-week-3 NFL and post-week-4 NCAAF state
are gone and cannot be reconstructed.

The 9/26 snapshot (9,658 rows) is the initial full copy, written the day the
script was created, when it was presumably run locally where .env exists.

## How to apply

1. **Never join team_stats_rolling to a past game.** Use
   team_stats_rolling_history with `snapshot_date < game_date` — latest snapshot
   before the game. That is what the script's docstring means by "the as-of-date
   query is unchanged either way".
2. **Wrap snapshot_team_stats in run_step.sh.** A history-builder that fails
   silently loses data permanently, and this one lost two football days before
   anyone noticed. Bare `python x.py` in a workflow is how.
3. **The EPA/SP+ question stays open** until history covers enough games. With
   change-only appends and football moving weekly, that is a few weeks of
   snapshots, not days. Do not re-test it against the matview.

Related: [[project_football_engine_audit_929]],
[[project_signal_calibration_gap_929]], [[project_prop_l5_leak_922]],
[[project_sp_plus_backtests_are_leaky_926]] — the same leak class, third time.

## RESOLVED SAME DAY — the leak-free answer is NO EDGE

The 2026-09-26 snapshot carries `captured_at = 2026-09-26T16:31:00Z` (12:31pm ET
Saturday), which is strictly BEFORE 54 of that day's NCAAF kickoffs and all of
Sunday's NFL. That makes a genuinely leak-free test possible, and it was run:

    48 games with snapshot data on both sides (NCAAF 36, NFL 12)

    NCAAF   19-17   52.8%   z=+0.05
    NFL      7-5    58.3%   z=+0.41   (n=12)
    ALL     26-22   54.2%   z=+0.25

Quartile ranking is NOT monotonic — NCAAF runs 55.6 / 33.3 / 55.6 / 44.4, which
is noise. Against the leaked version (70.4 / 64.8 / 45.1 / 38.0, z=+4.07) the
same lens collapses from 67.8% to 52.8%.

**The entire effect was the leak.** This independently confirms the 9/26
study_stat_edge conclusion and extends it to EPA: rolling-stat EPA differential
carries no ATS edge. n=48 is small, but the collapse from z=+4.07 to z=+0.05 is
the finding, not the absolute number.

DO NOT build a lens on rolling-stat EPA. If it is revisited, use
team_stats_rolling_history with `captured_at < kickoff_utc` — not snapshot_date
alone, since the 9/26 snapshot and the 9/26 games share a date and only the
timestamp separates them.
