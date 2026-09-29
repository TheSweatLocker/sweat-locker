---
name: Auto-fade calibration system
description: Cohort-based gate that suppresses or flips picks where the model is systematically wrong. Calibration data refreshed monthly.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**Concept:** Some pick types our model produces lose money systematically. Rather than expose those losing picks to users, auto-fade either silently suppresses them or flips the displayed pick to the OPPOSITE side (when sample warrants).

**Module:** `mlb_pipeline/auto_fade.py`

**Wired into:**
- `play_of_day.py` — Tier 1 HIGH CONVICTION filter + per-game ML lean determination
- `generate_daily_degen.py` — ML leg pool filter

**NOT wired into (intentional):**
- Dawg of the Day — its own product, dog plays are the explicit point. Framed as speculative.
- NRFI / totals — different model, different calibration needed.

**Cohort buckets (6 total):**

| Cohort | Definition | n | Hit rate | Action |
|---|---|---|---|---|
| `ml_chalk_high_mag` | Model + market both pick same team, corrected \|delta\| ≥ 1.5 (Marlins-style) | 7 | 85.7% | SURFACE |
| `ml_chalk` | Model + market both pick same team, magnitude small | 32 | 59.4% | SURFACE |
| `ml_dog` | Model picks against market (both ML and RL agree market is correct) | 24 | 25.0% | SUPPRESS |
| `ml_dog_high_conv` | Model picks against market with confluence_net ≥ 2 | 6 | 0.0% | SUPPRESS |
| `ml_fav_rl_dog` | Model picks ML-fav-but-RL-dog team (Orioles 4/25) — bookmakers split signal | 0 | unknown | SUPPRESS until calibrated |
| `ml_dog_rl_fav` | Model picks RL-fav-but-ML-dog team (rare pitching duel) | 0 | unknown | SUPPRESS until calibrated |

**Action thresholds:**
- FADE (silent flip): n ≥ 30 AND hit ≤ 0.30 (high bar — we're betting AGAINST our model)
- SUPPRESS (drop pick): n ≥ 5 AND hit ≤ 0.30, OR no calibration data at all (mixed cohorts)
- SURFACE (default): all other cases

**Key engineering decision:** mixed cohorts (`ml_fav_rl_dog`, `ml_dog_rl_fav`) default SUPPRESS because we have no historical calibration. This is conservative — we may be missing some real edges, but better that than surfacing untested cohorts.

**Recalibration cadence:** monthly. Run a script that pulls last 60-90 days of resolved games, recomputes hit rate per cohort, updates `CALIBRATION` dict in `auto_fade.py`. As samples grow, mixed cohorts may move to SURFACE or FADE based on actual performance.

**Why:** User explicitly asked "if we know we are wrong in any model >70% we should definitely use the other side." This is the operational answer — silent flip when sample warrants, suppress when it doesn't, surface what works. Frames as "model improving" rather than exposing internal uncertainty to users.

**How to apply:** Any new pick-generating module should pass through `auto_fade.adjust_pick()` before exposing the pick to users. When calibration data shifts (new audit results), update the `CALIBRATION` dict — no code changes needed downstream.
