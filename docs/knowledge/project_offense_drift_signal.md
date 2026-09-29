---
name: Offense drift signal — cold bats fade gate (queued 2026-05-07)
description: Add L10 vs season R/G drift as confluence vote + props fade gate; identified after 5/6 Twins miss
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Tomorrow add offense drift signal — diagnoses "good season offense, cold bats currently" trap that contributed to 5/6 Twins-correlated miss (Larnach Over, Buxton Over, Hoerner Over, Twins Bucket all lost together).

**Why:** mlb_game_context already stores home_runs_per_game (season) AND home_last10_runs_per_game, but no drift signal is computed or used. Twins were season-good (~102-105 wRC+) but in a cold streak — model didn't differentiate. Same data exists in fields, just no gate reading it.

**How to apply:** Tomorrow's session, alongside the pitcher_vs_team confluence extension:
1. Add `home_offense_drift` / `away_offense_drift` = L10 R/G - season R/G in game_context.py write path. No new API fetch needed.
2. Vote in signal_confluence_breakdown — drift > +1.0 = home/away depending on which side, drift < -1.0 = vote against that side.
3. Add fade gate in pipeline_props.py for hits-OVER picks when offense_drift < -1.0 R/G — suppress PRIME tier or downgrade to LEAN. Single-team prop stacks on cold offenses are the specific pattern to break.
4. Audit cohort: classify_offense_drift_cohort in audit_tier_calibration.py — track drift_negative_lt-1 cohort hit rate on hits-OVER picks, validate the gate is correctly calibrated.

True rolling wRC+ (L5/L10) was considered but rejected — requires park-normalized wOBA computation, marginal additional signal over R/G drift. Ship the cheap version first, audit, upgrade if it proves load-bearing.

Estimated effort: ~90 min implementation + smoke test.
