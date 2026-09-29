---
name: nfl-ncaaf-launch-readiness-725
description: "NFL Sept 4 + NCAAF Aug 22 launch prep shipped 7/25 — Week 1 stats fallback, real Jerry rules, app badge, POTD gate audited. NCAAF still needs same Week 1 fallback port."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-26T03:52:57.366Z
---

**Set 2026-07-25 end of session.**

## Shipped tonight (all live on origin/main)

**SHA `98c445b` — Sept 4 readiness (batch 1)**
- NFL Jerry rules block (`20260725_nfl_jerry_rules.sql`): replaced
  7/21 "no NFL model active yet" placeholder with real 3-sentence
  rules — EPA/CPOE + heavy_home_dog cohort + LEAN-cap disclosure.
- **NFL Week 1 stats_source fallback** (`nfl_game_context.py`):
  `load_team_stats_with_fallback()` detects thin current season
  (< 4 games/team avg), falls back to prior season regressed 0.6/0.4
  toward league mean. New `stats_source` column tracks: `current` |
  `prior_season_regressed` | `preseason` | `none`. Migration:
  `20260725b_nfl_stats_source.sql`.
- Preseason skip: events tagged `stats_source='preseason'` +
  `season_type='PRE'`, `primary_play=None`.
- Workflow `-f` guard cleanup (dead code from pre-Phase-1).

**SHA `5832788` + `f635b17` — validation + adjacent (batch 2)**
- Test harness (8/8): `scratchpad/test_nfl_fallback.py` — mocks
  Supabase and validates full fallback chain including
  heavy_home_dog PRIME preservation and preseason None-return.
  Real numbers: KC pass_epa 82.5 → 61.2, NYG -42 → -13.5 (60/40
  shrink to league mean).
- NCAAF Jerry rules row (`20260725c_ncaaf_jerry_rules.sql`) —
  upsert-safe (uses `ON CONFLICT (name, sport) WHERE is_active`).
  SP+/EPA/success_rate signals + NCAAF cohorts.
- App `nflGameContextMap` + amber `stats_source` badge on NFL
  Team Stats tab. Closes the loop with Jerry's LEAN-cap language.
- Plugged LIGHT-tier stale gap in `compute_primary_play` —
  case 4 (`abs_edge >= 2.0`) was NOT stale-gated while cases 2/3
  were. Meant weaker signal could sneak into `lock_of_week` while
  stronger STRONG-tier signal got capped. Now all non-cohort tiers
  cap at LEAN in stale mode consistently.

## Cohort-primary design principle (worth remembering)

Stale-stats mode does NOT suppress everything — the
`heavy_home_dog` PRIME override still fires because it's
Vegas-driven (only needs `close_spread`), not EPA-driven. Audit
validates it on 2022-2025 data at 65.4% (n=81). This is why we
still have a PRIME pick on Week 1 despite prior-season data
feeding the EPA calcs. Pattern: **cohort plays with market-only
inputs are exempt from stats_source caps**. Apply the same when
we port to NCAAF (heavy_home_dog_14+/7_13 cohorts qualify).

## Followups queued

- **NCAAF Week 1 stats_source fallback** — same problem, same
  fix pattern as NFL. Aug 22 season, ~40 min port.
- **Panel-implied surface for NFL** — MLB has it, NFL doesn't.
  Post-launch.
- **NCAAB Phase 1d** — Barttorvik + Monte Carlo + rest-days.
  October target.
- **Daily KenPom snapshots** — fixes end-of-season bias in
  cohort SU rates. October pre-NBA.

## Live state

- NFL prompt_templates row: **UPDATED** (real rules)
- NCAAF prompt_templates row: **UPDATED** (upsert applied)
- `nfl_game_context.stats_source` column: **LIVE**
- App `nflGameContextMap` fetch + badge: **LIVE on main**
- Test harness: `scratchpad/test_nfl_fallback.py` (session-local,
  gitignored — re-create if needed)

## Related

- [[project_nfl_phase1_ready_721]] — original Phase 1 audit
- [[project_ncaaf_phase1_complete_723]] — NCAAF foundation
- [[feedback_migration_pgrst_reload]] — every migration ends
  with NOTIFY pgrst, 'reload schema'
- [[feedback_always_push_after_commit]] — commit → push same
  turn; verify SHA on origin
