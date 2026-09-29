---
name: NFL Phase 1 audit baselines (2022-2025, 1139 games)
description: Phase 1 audit cohort hit rates discovered after backfill; heavy_home_dog at 65.4% on 81 games is the headline edge for Phase 2
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
NFL Phase 1 cohort baselines computed 2026-05-07 over 1139 games (2022-2025 regular + post). All eight cohorts now flow into mlb_tier_calibration with sport='nfl', refreshing daily on cron.

**Why:** Before building Phase 2 NFL picks (props pipeline, weekly slate cards), we needed audit-validated baseline hit rates for the cohorts the model will gate on. Same discipline that caught OVER-lean (0-10), NBA NR_gap (33%), K-Under PRIME (55.6%) — verify the cohort first, build picks second.

**How to apply:** When designing Phase 2 NFL picks (June-July build), lean into:

1. **nfl_heavy_home_dog: 65.4% (53-28) on 81 games** — biggest edge surfaced. Home underdogs of +7 or more covered 65%. Classic "letdown spot for road favorite" pattern. Phase 2 should build a "home dog +7" tier with audit-validated PRIME conviction.

2. **nfl_outdoor_under: 52.1% (390-359 of 749)** — weather suppresses scoring outdoors. Slight UNDER lean baseline; combine with cold/wind for stronger signal.

3. **nfl_div_home_cover: 48.6% (188-199)** — slight away edge in division games. Counter-narrative to "anything can happen in divisional matchups."

Baselines that hit ~50% (no edge): home_fav_cover (50.1%), home_dog_cover (51.0%), heavy_home_fav (49.3%), dome_over (51.3%), rest_advantage_cover (48.5%).

The rest_advantage signal hitting 48.5% is notably counterintuitive — "rested team has edge" is mostly noise on 4-season sample. Don't gate on this without much larger n.

Commit: e84956d (sign convention fix) + this audit cohort wiring.
