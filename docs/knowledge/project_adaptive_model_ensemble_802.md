---
name: project-adaptive-model-ensemble-802
description: "Adaptive backtest-driven ensemble weighting workstream — Jerry weights each model by lifetime + recent hit rate instead of treating them equally. Kickoff 2026-08-02."
metadata:
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-02T01:56:14.719Z
---

Adaptive model-ensemble weighting workstream kicked off 2026-08-02 after
8/1 audit surfaced concrete performance divergence between models:

  PANEL   62.5% W  ROI +19.3%   (top performer, currently under-weighted)
  LENS_ML 60.0%    ROI +14.5%
  CONF_V1 57.1%    ROI +9.1%
  MC      55.0%    ROI +5.0%    (baseline)
  V4      46.9%    ROI -10.5%   (LOSING money, currently over-weighted)

Small sample (n=40) but directionally clear.

**Vision:** Jerry synth weights each model's directional signal by its
current hit rate + recency-adjusted performance. Weights derived from
backtest, not intuition. Models that lose favor lose weight; models on
a hot streak gain weight. Self-correcting system that adapts to model
drift or regime shift.

**Trigger for the workstream:** 8/1 ARI @ CLE game where Jerry called
CLE at 72 conv (citing "2/3 models on HOME") while PANEL was actually
on AWAY (ARI won 9-2). Jerry weighted MC + V4 + externals over the
sharper PANEL + CONF_V1 signals — exactly the wrong direction given
the audit numbers.

**Architecture:**

Phase 1 — Data foundation (1-2 days):
  Table `model_track_records`: sport, model_name, market, direction,
  window (lifetime/90d/30d/14d/7d), wins/losses/hit_rate/roi_est,
  computed_at. Nightly populator recomputes from graded
  mlb_game_context history.

Phase 2 — Backtest weighting schemes (2-3 days):
  `_backtest_ensemble_weights.py` retro-tests 4-5 schemes over
  historical games:
    1. Equal weight (baseline)
    2. Accuracy weight (hit_rate / sum)
    3. Log-odds Brier weight
    4. Recency-decayed (exponential decay 14-30d)
    5. Regime-specific (fav vs dog, home vs road)
  Winner is whichever ships the highest ROI on holdout data.

Phase 3 — Wire into Jerry synth (1 day):
  Load weights from model_track_records at synth start. Inject into
  prompt: "Model weights (last 30d): PANEL 2.3x · MC 1.0x · V4 0.4x —
  weight your synthesis accordingly." OR compute a weighted
  `ensemble_pick` per game and hand Jerry both individual + ensemble.

Phase 4 — Continuous learning (ongoing):
  Weights recompute nightly from fresh graded data. Auto-correcting.

**Why not ship it 8/1:**
  - Small n=40 today = noise risk if we ship weights immediately
  - Historical model predictions per game only preserved in rolling
    game_context window (221 rows). Need preservation strategy.
  - Prompt-engineering risk — could confuse Jerry more than help
    without proper prompt-eval

**Interim manual patch (safe to ship any time):**
  Update Jerry synth prompt to explicitly say: "PANEL is our top model
  (62.5% W recent), weight it heaviest. V4 is under-performing
  (46.9%), treat as tiebreaker only." Manual weighting via prompt =
  90% of value at 10% of build cost.

**Related infra already shipped:**
  - `signal_predictions` table (2026-08-01c) — atomic prediction log
  - `signal_track_records` table (same migration) — rolling aggregates
  - `bucket_roi_lookup.py` — pattern for cross-sport lookup + Path B
    smart-injection gates
  - Both are the foundation this workstream extends

**How to apply:**
  - Post-launch scope (Week 2-3), after Monday's website work
  - Backtest MUST run before shipping weights to Jerry — no guessing
  - Ship manual prompt patch first for immediate improvement, then
    replace with full ensemble later
  - Related: [[project_v2_ensemble_models]] (5-model consensus vision),
    [[project_model_reweight_721]] (60d reweight analysis), 
    [[project_composite_debias_finding_712]] (Jerry debiased 60.5% beats
    composite 52.8% — same theme)
