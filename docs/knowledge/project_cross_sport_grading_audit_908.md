---
name: project-cross-sport-grading-audit-908
description: "🎯 9/8 audit: MLB/NCAAF/UFC grading wired; NBA missing resolver; NHL missing both resolver + grader entry. NHL/NBA/NCAAB have no data yet (offseason) — fix before season starts."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T14:22:14.761Z
---

**Cross-sport grading infrastructure audit · 9/8 afternoon.** After NCAAF grading fix + POTD grader + morning_brief.py shipped, audited what's ready for the sports still offseason (NBA/NHL/NCAAB) so we ship fixes BEFORE their seasons open.

## Current state per sport

| Sport | Resolver Script | grade_jerry_reads entry | Live grading | Season status |
|---|---|---|---|---|
| **MLB** | ✅ resolve_game_results.py | ✅ 'MLB' entry | ✅ 100% coverage nightly | In-season |
| **NFL** | ✅ resolve_nfl_results.py | ✅ 'NFL' entry | ✅ working (audit clean) | Week 2 upcoming |
| **NCAAF** | ✅ resolve_ncaaf_results.py | ✅ 'NCAAF' entry | ✅ 67% Sat 9/6 after 9/8 fix | In-season |
| **UFC** | separate: ufc_grader.py + grade_ufc_jerry_reads.py | separate path | ✅ 30/30 recent graded | In-season |
| **NCAAB** | ✅ resolve_ncaab_results.py | ✅ 'NCAAB' entry | untested (0 games in DB) | **Season Nov 3** — 56 days |
| **NBA** | ❌ **MISSING** | ✅ 'NBA' entry | untested (0 games in DB) | **Season Oct 24** — 46 days |
| **NHL** | ❌ **MISSING** | ❌ **MISSING** | untested (0 games in DB) | **Season Oct 8** — 30 days |

## What's needed BEFORE each season starts

### NHL (Oct 8, 30 days) — MOST URGENT
- Build `mlb_pipeline/resolve_nhl_results.py`: pulls NHL API scores, PATCHes/UPSERTs `nhl_game_results` (rebuild wrap 8/17 confirmed table exists)
- Add `'NHL': 'nhl_game_results'` to `grade_jerry_reads.SPORT_RESULT_TABLE`
- Add `'NHL': 'spread_result'` (or equivalent) to `SPREAD_COL_BY_SPORT`
- Wire resolver into `nhl_pipeline.yml` overnight
- Verify with real Week 1 game once season starts

### NBA (Oct 24, 46 days)
- Build `mlb_pipeline/resolve_nba_results.py`: ESPN scoreboard client (per project_nba_rebuild_status_817) already exists — leverage its game_id format
- `grade_jerry_reads` already has NBA entry, so just needs resolver to feed results table
- Wire into `nba_pipeline.yml`

### NCAAB (Nov 3, 56 days)
- Resolver + grader both present. Just needs first-week verification when season starts. Add to nightly morning_brief.py so we catch any surprise gap.

## Recommended sequencing

- **Week of 9/8-9/13:** finish current in-season fixes (NFL prop signals, LR shadow promotion)
- **Week of 9/22-9/27:** build NHL resolver + grader entry (2 weeks before season)
- **Week of 10/6-10/11:** build NBA resolver (2 weeks before season)
- **Week of 10/20-10/25:** NCAAB verification pass (2 weeks before season)

## Data pipeline dependencies to verify per sport (pre-season)

For each sport before its season starts, verify:
1. Game ingest → writes rows to `{sport}_game_context` with correct game_id format
2. Odds ingest → writes closing lines to context
3. Post-game score ingest → writes to `{sport}_game_results` OR resolver PATCHes them
4. Grader reads results and updates jerry_reads.result
5. `aggregate_daily_records` includes the sport
6. `daily_surface_records` gets rows for the sport
7. Surface record windows (mtd/d7/d30) rollup includes the sport

Morning brief.py already checks steps 3-6 automatically via Section 1 gaps.

## Related
- [[project_data_infrastructure_priorities_908]] — Priority 2 (grading for all sports) — this memory expands scope
- [[project_ncaaf_grading_gap_908]] — pattern for how to close a grading gap (mascot expansion + date backfill)
- [[docs/MORNING_BRIEF.md]] + [[docs/scripts/morning_brief.py]] — one-command detector for any regression
