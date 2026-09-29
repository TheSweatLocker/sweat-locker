---
name: project-v5-retrain-no-change
description: "2026-05-21 v5 retrain attempt (XGBoost runs model) — marginal data growth (+7%) didn't move metrics. v4 stays live. Real v5 win needs new features."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Ran v5 walk-forward retrain 2026-05-21 with same features as v4 + 45 additional games (679 vs 634, +7%). Result: **no improvement, marginal degradation.** v4 kept live.

**Numbers (walk-forward, n=479 holdout):**
- Home MAE: 2.273 (v5) vs 2.225 (v4) — v5 worse by 0.05
- Away MAE: 2.492 (v5) vs 2.485 (v4) — v5 worse by 0.01
- Spread MAE: 3.383 (v5) vs 3.353 (v4) — v5 worse by 0.03
- Total MAE: 3.299 (v5) vs 3.257 (v4) — v5 worse by 0.04
- Direction accuracy: 62.4% both — flat

**Lesson:** marginal data growth (+7%) with same feature set = noise, not signal. v4 is well-calibrated and a 1-month-newer dataset doesn't move it.

**What WOULD move v5:**
1. **Add `prop_net_signal` as a feature** — requires 2-3 more months of prop history to have enough samples per game (currently only ~100 games have prop data, need 400+ for the feature to be useful as a training input)
2. **Add v4 prediction as a meta-feature** — would need careful out-of-fold setup to avoid leakage; possibly stacking architecture
3. **Different model class** — LightGBM, neural net — research project
4. **Engineer new features** — interaction terms (xera_gap × bp_fatigue, wrc_diff × park_run_factor), rolling cohort indicators

**v4 confidence bucket validation (good news from this retrain):**
- |spread| > 2.0: **69.8% direction acc** (n=159) ← validates the spread_delta trap zone fix we shipped same day (raised STRONG ML threshold from 1.5 to 2.0)
- |spread| > 1.5: 65.9%
- |spread| > 1.0: 65.1%

The model self-confirms the cohort cliff at delta ≥2.0.

**Operational state:**
- Production v4 untouched (ran train script with `--no-save`)
- Backup files created: `models/home_runs_model_v4.pkl`, `models/away_runs_model_v4.pkl`, `models/runs_model_meta_v4.json`
- Next v5 attempt: defer to August (post-launch, full season + prop data accumulated)

**When to revisit:** July/August. By then we'll have:
- ~3 months of prop data (300-500 resolved props per game-date with prop signals)
- Full season of v4 predictions for stacking/meta-feature work
- Enough sample to add the prop_net_signal feature meaningfully

Related: [[project_spread_delta_trap_zone]] — independently validated by the buckets here.
