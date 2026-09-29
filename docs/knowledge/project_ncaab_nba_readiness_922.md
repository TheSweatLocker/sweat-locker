---
name: project_ncaab_nba_readiness_922
description: "NCAAB + NBA readiness answered 2026-09-22. Recovered 5,027 NCAAB and 2,519 NHL closing lines, graded 8,744 games across 3 sports. NBA props NOT ready (no game logs, no proj_minutes) — ship sides/totals only Oct 21."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-22T04:14:06.484Z
---

Two of Andy's queued discussion items, answered
([[project_discussion_queue_921]]). Split tab and the v1.0.2 backlog
reconciliation still pending.

## THE PATTERN — three sports, lines present, nothing graded

The single biggest finding. NHL, NCAAB and NBA all had closing lines
either stored or cheaply recoverable, and `spread_result` /
`total_result` on **ZERO** rows.

Shared cause: each sport's resolver grades only the date it runs on and
reads the line from the **context** table. Any game without a context row
at the time was therefore never graded — while `<sport>_game_results` was
carrying the line it needed the whole time. Nobody looked there.

NBA is the cleanest illustration: 1,239 of 1,365 rows already had
`close_spread` and needed no backfill, no API call, no new source.

Fixed 2026-09-22 (commits d1a5411c, cbcb70b1, fb33445a, 6e0b88b2):

    NCAAB  5,027 lines recovered · 5,027 graded   (40k credits)
    NHL    2,519 lines recovered · 2,519 graded   (60k credits)
    NBA        0 needed          · 1,198 graded   (free)

Distributions all textbook, which is the real check that recovered lines
are sound: NCAAB 49.7% home cover / 50.6% over; NBA 49.96% / 53.7%;
NHL 47.6% on a +/-1.5 puck line.

`backfill_game_grades.py` is now the one grader for all three —
NHL and NCAAB previously had the same rule written two different ways
(`(h-a)+puckline>0` vs `(h-a) > -close_spread`, algebraically identical).

## NCAAB readiness (season 2026-11-03): NOT ready, outcome layer fixed

**Healthy:** team_efficiency and team_stats at 2025-26, rating_snapshots
updated daily, 365 aliases, and a full script ecosystem (KenPom/Torvik/
Haslam pulls, efficiency model, MC simulator, LR trainer, panel
predictor, resolver, game reads, externals).

**Still blocked — signal validation.** All 65 signals remain
`UNVALIDATED`, `sample_n=0`. Grading was necessary but NOT sufficient:
`refit_signal_registry` needs signal FIRINGS (what each signal said per
game), which needs as-of feature values, and `ncaab_rating_snapshots`
only reaches back to **2026-08-14**. Confirmed by running it —
"0 signals meet min_n=20". I initially over-claimed that the odds
backfill would unlock validation; it unlocked the outcome half only.

So Nov 3 is the 8/20 report's **Scenario B**: launch with unvalidated
weights and self-heal. Difference now is that every graded game counts
toward that, and cohort/ATS/OU tendency work is available from day one
(verified: favourites cover 46-51% by spread bucket, every CI containing
50 — an efficient market, i.e. sane data).

Open question if more sample is wanted pre-launch: source historical
as-of ratings for 2024-25.

## NBA props (season 2026-10-21): SHIP SIDES/TOTALS, HOLD PROPS

Market availability is NOT the constraint — props are already live for
opening night (8 families: pts/reb/ast/threes/blocks/steals/turnovers/
PRA; thin at 2 books / 5 players a month out, which is normal).

**Two disqualifying data gaps:**
  * `nba_player_game_logs` = **0 rows** -> no L5/L10 recent form, which
    is the backbone of prop analysis and what MLB/NFL props lean on.
  * `proj_minutes` = **0 / 1,804** -> no minutes projection. Minutes is
    the largest driver of any volume prop; without it you cannot handle
    a rotation change, rest day, blowout or teammate injury — exactly
    where lines are softest and being wrong is most visible.

Have: 527 usable season-level projections (Sleeper, fresh),
`nba_player_vs_team` 12,896 rows with career + recent splits, working
generator, `nba_pipeline_props` empty.

**Sides/totals ARE ready** and NBA is the best-calibrated of the three
upcoming sports — 1 VALIDATED / 6 DISCOVERY / 9 ANTI_VALIDATED, 30 of 64
signals carrying a sample (versus NHL 0/62 and NCAAB 0/65), now fed by
1,198 freshly graded games.

Both prop gaps are pullable: game logs from whatever feeds
`nba_player_vs_team`, minutes from the projections feed that is already
wired but returning nulls.
