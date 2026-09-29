---
name: feedback-grading-zero-fail-912
description: "🚨 Zero-fail grading discipline. Andy has raised grading-pipeline gaps every other day since launch. Every graded record MUST reflect actual boxscore outcomes by morning-after. No pending items for games that finished. No silent 'stat not found' skips. Every prop_type in DB must have a STAT_MAP entry. Every fetch must paginate past 1000-row PostgREST default."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-12T12:02:59.158Z
---

# Grading is a zero-fail process

Andy's exact words 2026-09-12 (bringing up for the Nth time):

> "The Sharp picking all the way to the grade (not being mixed up
> another app feature) is a zero fail process, needs to happen
> accurately like clockwork and every morning it doesn't do that!
> Same with the grading of the Sweat Card for yesterday when it's
> displayed on home screen."

> "There CAN'T be 8 fucking pending"
> "There should be no pending"
> "All the games yesterday have been played!!!"

## The three failure modes that recur

1. **PostgREST 1000-row default limit.** Any grader that fetches
   ungraded rows with `requests.get()` and no `Range` header caps
   at 1000. When prop inventory exceeds that (2962 on 9/11), the
   PRIME pitcher props at higher ids are NEVER SEEN by the grader.
   Root cause of 9/11 "5-0 · 3 pending" on Sweat Card display —
   grader ran, said "graded 1", never touched Molina/Kirby/Nola
   because their rows were past row 1000.

2. **Missing STAT_MAP entries.** `grade_props.STAT_MAP_MLB` only
   had pitcher props + `hits_over` for a long time. Every
   `hits_under`, `total_bases_*`, `rbis_*`, `runs_*`, `hr_*`,
   `batter_ks_*` prop shipped, was displayed to users, and stayed
   result=NULL forever because grade_prop returned `None` for
   unknown prop_types and the SKIP counter absorbed it silently.
   Fix landed 9/12 in [mlb_pipeline/grade_props.py](mlb_pipeline/grade_props.py).

3. **Aggregator silently drops ungraded items.** `agg_sharp_card`
   used to skip items with `verdict=None`, so 8 pending pitcher
   props on The Sharp shipped list vanished from the day-record
   entirely. Display showed 4-2-1 when the truth was 4-2-1 (7 of
   15 graded, 8 pending). Fix landed 9/12 — pending count now
   surfaces in `detail.pending` and `pick_count` = total shipped.

## Rules for every future grader / aggregator touch

- **Fetch pagination**: any query against a table where daily row
  count could exceed 1000 must use `Range: 0-999` / `1000-1999` /
  etc. until fewer than 1000 return. Never trust the default limit.
- **Order the query**: `order=id.asc` (or another stable key) before
  paginating, so chunks don't overlap or skip.
- **Every prop_type must resolve**: when a new prop_type is added to
  the ingestion pipeline, the STAT_MAP entry must land in the SAME
  commit. Grep for grade_props.STAT_MAP_MLB before merging a new
  prop scraper.
- **Aggregators expose pending count**: any daily-record aggregator
  that runs before all items grade MUST separate `wins/losses/pushes`
  (graded outcomes) from `shipped` and `pending`. Display code
  reads all three so the user sees "4-2-1 · 8 pending" not "4-2-1"
  with a fake denominator.
- **Silent skip categories are landmines**: any counter like
  `skipped_no_stat` that reaches non-trivial numbers on a normal
  day is a bug. Add a `--verbose` mode that prints the first N
  skip reasons so future regressions surface fast.

## When Andy asks about grading

His baseline expectation: **by 8am ET the morning after game day,
every completed game's picks show a graded outcome on the app.**
Any pending count > 0 for a completed slate is a failure state
that needs a same-day fix + memory update explaining why.

## Related

- [[feedback_morning_audit_format_912]] — audit must show ungraded
  count if > 0, never overstate the graded-only hit rate
- [[project_nfl_game_id_mismatch_911]] — sibling landmine class,
  same pattern (silent-skip due to key mismatch)
- [[feedback_sharp_card_composite_record]] — Sharp Card = sides +
  props combined; both must grade for the record to be honest
