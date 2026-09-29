---
name: nfl-phase1-ready-721
description: "NFL Phase 1 much further along than expected — 6 modules already written, schema applied. Aug 7 preseason target realistic in 4 focused days. Sept 4 Week 1 target 25 focused days."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  audit_date: 2026-07-21
  modified: 2026-07-22T02:23:24.715Z
---

**Set 2026-07-21 after NFL foundation audit.**

## Ground truth — a lot more exists than assumed

Convention: NFL files live in `mlb_pipeline/` with `nfl_` prefix (same as NBA/NCAAB/UFC). No separate `nfl_pipeline/` directory needed.

**Already shipped (as of today):**
- `mlb_pipeline/nfl_pipeline.py` (187 lines) — team stats puller (nflverse) ✓
- `mlb_pipeline/nfl_player_stats.py` (186 lines) — player-week puller ✓
- `mlb_pipeline/nfl_backfill_results.py` (225 lines) — 2022-2025 backfill · **needs to be RUN**
- `mlb_pipeline/nfl_seed_aliases.py` (152 lines) — 32-team alias seed · **needs to be RUN**
- `mlb_pipeline/nfl_weekly_card.py` (241 lines) — weekly card skeleton (partial)
- `mlb_pipeline/generate_nfl_game_reads.py` (324 lines) — Jerry Haiku game reads ✓

**Schema applied:**
- `supabase/migrations/20260507_nfl_foundation.sql` — aliases + team_stats + game_results
- `supabase/migrations/20260507b_nfl_player_stats.sql` — player-week schema

## Immediate bootstrap (~1 hour work, TODAY)

Run in order:
1. `python mlb_pipeline/nfl_seed_aliases.py` — one-shot alias table populate
2. `python mlb_pipeline/nfl_backfill_results.py --start 2022 --end 2025` — ~1100 games, 10 min

Once bootstrap complete: cohort baselines from `project_nfl_phase1_audit_baselines_507.md` will be live in mlb_tier_calibration.

## Aug 7 preseason target (4 focused days)

MVP scope: schedule + market lines + weekly card showing games, NO POTD/props.

Files needed:
1. **`nfl_odds_pull.py` — NEW** (~180 lines, half day) — Odds API `sport=americanfootball_nfl` for live lines
2. **`nfl_cohort_backfill.py` — NEW** (~250 lines, half day) — computes 10 audit cohorts + writes to mlb_tier_calibration
3. **`nfl_weekly_card.py` — FINISH** (~200 more lines, 1 day) — schedule-only mode + lock_of_week + weekly_parlay
4. **`.github/workflows/nfl_pipeline.yml` — NEW** (~50 lines, half day) — weekly cron per section 6 in plan

## Sept 4 Week 1 target (25 focused days total)

Full parity with MLB. Files to ship (in addition to above):
5. `nfl_game_context.py` — per-game feature assembler (~400 lines, 1.5 days). Model on `ncaab_game_context.py`.
6. `nfl_play_of_day.py` — POTD selector (~600 lines, 2 days). Model on `nba_picks_generator.py`, NOT `play_of_day.py` (MLB-specific).
7. `nfl_generate_props.py` — passing/rushing/receiving/anytime-TD (~700 lines, 3 days). New logic — no MLB port.
8. `nfl_props_migration.sql` — mirrors `mlb_props` schema (~50 lines, 1 hr)
9. `nfl_cohort_features.py` — nightly cohort recompute (~250 lines, half day)
10. `resolve_nfl_results.py` — post-game outcome resolver (~180 lines, half day)
11. `pull_externals_nfl.py` — port MLB template (~500 lines, 1.5 days)
12. `generate_nfl_sweat_card.py` — sport-specific sweat rendering (~400 lines, 1 day)
13. `nfl_snap_counts.py` — red-zone target puller (~120 lines, half day) [for anytime-TD]

**Timeline confidence:**
- Aug 7 preseason: **95% confidence** (4 days budgeted, 12 days available)
- Sept 4 Week 1: **70% confidence** (25 days budgeted, 32 available)
- Tail risk: prop pipeline complexity — descope anytime-TD to v1.1 if slipping

## Status 2026-07-21 evening — Phase 3 core loop SHIPPED

**Live in DB:**
- 32 aliases seeded (nfl_seed_aliases)
- 1139 games backfilled 2022-2025 (nfl_backfill_results)
- 76 rows in `nfl_game_context` (real EPA-based projections)
- 16,881 player-week rows in `nfl_player_stats` (backfilled 2022-2025)
- 184 team-season rows in `nfl_team_stats`

**Files shipped this session:**
- `nfl_odds_pull.py` — Odds API integration (flips native → nflverse sign)
- `nfl_cohort_backfill.py` — 10 cohorts (HEAVY_HOME_DOG 63.1% matches audit)
- `nfl_weekly_card.py` — lock/parlay/skip_alerts + schedule mode
- `.github/workflows/nfl_pipeline.yml` — 7-cron weekly schedule
- `nfl_game_context.py` (~370 lines) — EPA projections + 5-way confluence + primary_play
- `nfl_play_of_day.py` (~250 lines) — tier gate + lock_of_week + chalk-trap skip alerts
- `nfl_generate_props.py` (~380 lines) — L4+season+opp blend, edge% tiering

**Schemas applied by user:**
- `20260721_nfl_game_context.sql`
- `20260721b_nfl_game_picks.sql`
- `20260721c_nfl_props.sql`

**Sept 4 remaining work (~3 files):**
- `resolve_nfl_results.py` — post-game outcome resolver
- `pull_externals_nfl.py` — port MLB template (reuses shared _playwright_helper)
- `generate_nfl_sweat_card.py` — sport-specific sweat rendering

Props generator ran clean but returned 0 picks — Odds API doesn't
publish NFL player props until ~3 days pre-kickoff, so Week 1 props
appear Sept 6-7. Infrastructure validated via direct API probe.

Sept 4 confidence raised: **80%** given core loop done, remaining
files smaller than budgeted.

## Weekly cron cadence (proposed)

```yaml
- cron: '0 15 * * 2'   # Tue 11am ET  — nflverse schedule + opening lines
- cron: '0 22 * * 3'   # Wed 6pm ET   — externals pull + props v1
- cron: '0 18 * * 4'   # Thu 2pm ET   — TNF finalization (weekly card LOCK)
- cron: '0 14 * * 6'   # Sat 10am ET  — Sunday early-slate lock
- cron: '0 12 * * 0'   # Sun 8am ET   — final Sunday card
- cron: '0 14 * * 1'   # Mon 10am ET  — MNF finalization + Sunday resolver
- cron: '0 15 * * 2'   # Tue 11am ET  — full-week resolver + cohort grades
```

Matches user's external pull spec (NFL Thu 4PM + Sat 10AM) plus adds Wed 6PM external pull + Tue backfill.

## Cohorts to backfill (from May audit)

| Cohort | 4-season hit rate | Sample |
|---|---|---|
| `nfl_heavy_home_dog` (home +7+) | **65.4%** ⭐ | 81 games |
| `nfl_outdoor_under` (cold/wind) | 52.1% | 749 games |
| `nfl_div_home_cover` | 48.6% (slight away edge) | 387 games |
| `nfl_home_fav_cover` | 50.1% (noise) | — |
| `nfl_dome_over` | 51.3% (noise) | — |
| `nfl_rest_advantage_cover` | 48.5% (counterintuitive noise) | — |
| `nfl_heavy_home_fav` | 49.3% (noise) | — |
| `nfl_home_dog_cover` | 51.0% (mild) | — |

Baseline: `nfl_heavy_home_dog` is the audit-anchored PRIME candidate for v1 conviction gating.

## Related

- [[project_nfl_prep_plan]] — original May prep plan
- [[project_nfl_phase1_audit_baselines]] — 4-season cohort audit
- [[project_external_aggregation_launch]] — pull_externals_nfl needs port from MLB template
- [[project_launch_priorities_july]] — mid-Aug MLB launch, then NFL preseason
