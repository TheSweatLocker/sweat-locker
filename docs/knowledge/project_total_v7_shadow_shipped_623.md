---
name: project-total-v7-shadow-shipped-623
description: 6/23 shipped v7 total model in shadow mode (59% holdout, 64% conf-UNDER); 14d gate before production swap
metadata:
  type: project
---

User accepted shipping the v7 GB model in shadow mode after extensive retrain audit (6/23).

**Shipped (commit d54a3c0):**
- models/total_v7_gb.pkl: GradientBoost on 729 clean rows
- shadow_total_inference.py: scores each game with v7, writes jerry_cache[v7_total_shadow_<date>]
- audit_v7_shadow.py: v7 vs composite vs actuals
- Added to .github/workflows/mlb_pipeline.yml after play_of_day step

**Production rules:**
- p_over <= 0.40 = UNDER conf (64% hist) → eligible to publish
- p_over >= 0.60 = OVER conf (51% hist, no edge) → log only
- 0.40 < p < 0.60 = lean → log only

**Promotion gate:** 14 days of shadow data, then audit_v7_shadow.py compares v7 vs composite. If v7 net wins, promote to production card-eligible.

**Why:** Composite was at ~51% baseline, retrained ML hit 59% holdout. Real 8pp improvement.

**How to apply:** Don't change card publishing rules until promotion gate clears. Conf-UNDER is the only v7 signal that should ever influence a published pick.

**Backfills queued (not done):**
- pitcher_vs_team_era: stuck at 89% (372 missing games — name mismatches likely)
- first_inning_era: 22% (no backfill written yet)
- catcher_framing: 13% (separate source)
- snapshot pipeline fix so future games write ALL fields into mlb_game_results (root cause of all backfill pain)

**Why not 75%:** With current data we hit 58-59% ceiling. Path to 75% needs point-in-time Savant (today's was approximate), per-game lineup wRC+, weather forecast at game time. Each is a separate data engineering project.

Related: [[project_total_model_retrain_625]], [[project_v4_over_drift]]
