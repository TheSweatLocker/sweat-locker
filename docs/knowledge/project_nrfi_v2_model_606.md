---
name: nrfi-v2-model-606
description: 6/6 NRFI/YRFI replaced with sklearn LogisticRegression model trained on inning-1 features; top 10% hits 77.8% on holdout vs 44.4% old
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Shipped 6/6 (commit `81cf24e`).

**Trigger:** Morning audit flagged NRFI "declining" — 14-day PRIME 90-94 at 54.5% n=11 (down from 69% old expectation). Rather than just gate the existing nrfi_score tighter, treated it as a chance to RE-LEARN what predicts a scoreless 1st inning.

**The model:**
- sklearn `LogisticRegression(C=0.3)` for regularization
- n=448 graded games with full feature coverage (out of 855 with NRFI result; ~52% have all inning-1 offense splits populated, rest mean-imputed)
- Time-ordered 80/20 split — train on oldest 80%, validate on NEWEST 20% (no leakage)
- Same training pass produces YRFI weights

**Top learned features (signed weights):**
1. `away_off_inn1_wrc` -0.77 — away team's 1st-inn wRC+ is THE strongest predictor
2. `away_off_inn1_rpg` +0.53 — and their R/G in 1st
3. `away_team_k` -0.35 — high K teams swing big, score early via HR
4. `home_xera` +0.30 — home pitcher quality
5. `home_sp_k` +0.27

**Holdout validation (newest 20%, never seen):**
- Top 10% NRFI by new score: **77.8%** vs old 44.4%
- Top 20% NRFI by new score: 55.6% vs old 44.4%
- **Top 20% YRFI by new score: 77.8% predicting RUN in 1st** — actionable new surface

**Production thresholds:**
- v2 NRFI ≥70 → PRIME (lifetime 65.9% top 10%)
- v2 NRFI ≥60 → STRONG (lifetime 69.3% on 60-69 band)
- v2 YRFI ≥70 + max 1st-inn ERA ≥5.5 → STRONG (77.8% holdout)
- Legacy `nrfi_score 90-94 only` fallback if scorer fails to load

**Files:**
- [mlb_pipeline/_nrfi_yrfi_reweight_606.py](mlb_pipeline/_nrfi_yrfi_reweight_606.py) — training harness, re-run as sample grows
- [mlb_pipeline/nrfi_v2_scorer.py](mlb_pipeline/nrfi_v2_scorer.py) — production scorer `score_nrfi(ctx, home_off, away_off)`
- [mlb_pipeline/models/nrfi_v2_weights.json](mlb_pipeline/models/nrfi_v2_weights.json) — learned weights + scaler params
- [mlb_pipeline/generate_daily_degen.py:286-410](mlb_pipeline/generate_daily_degen.py#L286-L410) — wiring

**Why:** User explicitly pushed back when first attempt was just rebanding the old score. They wanted real reweighting + backtest leveraging all the features we collect. The model surfaces real signal — the old nrfi_score treated SF@CHC as PRIME 100 NRFI without seeing that Cubs hits stack 1st-inn wRC+ was elite (78). The new model gives that game v2 score 28 = SKIP.

**How to apply:** When NRFI looks degraded again on a short window, the answer is probably retraining (re-run `_nrfi_yrfi_reweight_606.py` on the larger sample) not removing the band. Production: when a Jerry read or sweat card mentions an NRFI play, the v2 score is in the candidate dict as `nrfi_v2_score` / `yrfi_v2_score`.

Linked: [[feedback-no-nrfi-on-cards]] (still applies — public cards stay NRFI-free, this only affects Daily Degen), [[project-nrfi-patterns-april]] (cold temp ≤45°F still validated as a top-feature factor in the regression).
