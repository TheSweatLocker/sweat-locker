---
name: ncaaf-lr-total-dead-914
description: "✅ v1.05 RETRAIN 2026-09-15. NCAAF LR total no longer inert — model now spreads p_over across [0.456, 0.612] on live slate (was stuck [0.458, 0.550] on 82/82 games), fires STRONG_OVER on ~10% of games. Chronological split shows +2.5pp lift. Full EPA/SP+/rolling backfill queued as v1.06."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T17:17:54.730Z
---

## ✅ RESOLVED (partial) 2026-09-15 — v1.05 retrain

Trainer: `mlb_pipeline/ncaaf_total_logreg_train.py` rewritten. Model at `models/ncaaf_total_logreg.json`.

**Fixes shipped:**
- Chronological train/test split (was random-shuffle → optimistic leakage across seasons)
- Added ctx features: `neutral_site`, `conference_game`, `week`
- Trainer computes rolling L4 team stats but omits them from FEATURES until ncaaf_game_context persists them (queued v1.06)
- Pull expanded 10k → 25k row pagination (6,207 resolved games now vs prior cap)

**Live verification on 2026-09-01 → 2026-09-20 slate (199 games):**
- **p_over spread: [0.456, 0.612]** (was [0.458, 0.550] — a coin-flip band on every game)
- **19 STRONG_OVER** picks fire on 199 games (~10% actionable) vs prior 0
- Test acc 51.8% vs baseline 49.3% = **+2.5pp lift** (chronological split, honest)

**Downstream:** `apply_ncaaf_total_lr_override` (defensive_gates.py) in shadow-only mode. Signal now differentiates games rather than uniformly saying "coin flip."

## ✅ v1.06 shipped 2026-09-15 · commit 4ca454d3

Rolling L4 team form (PPG/PA/total-avg/over-rate per team) now:
- Computed in `ncaaf_game_context.load_team_rolling_form()` at pipeline run-time
- Stamped onto every `ncaaf_game_context` row via new `home_l4_*/away_l4_*` cols
- Persisted by migration `20260915b_ncaaf_ctx_l4_rolling_form.sql` (⚠️ must be applied via Supabase SQL editor — see `_apply_20260915b_ncaaf_l4_rolling.md`)
- Read by `_lr_predict_total` at inference (feature names match)
- Trainer re-enables ROLLING_FEATURES in FEATURES list

**Live inference on 2026-09-01→9-20 (pre-migration, cols still resolve to imputer medians):**
- p_over spread: [0.435, 0.643] (v1.05 was [0.456, 0.612])
- STRONG picks fire: 52/199 = 26% (v1.05 was 19/199 = 9.5%)
- Trade-off: 2.5× more coverage, slightly lower per-pick edge (test acc 50.3% vs v1.05's 51.8%)
- Downstream `apply_ncaaf_total_lr_override` runs demote-only → more STRONG signal = fewer legacy demotions

**Follow-ups:**
1. Apply migration 20260915b in Supabase SQL editor
2. Run `python ncaaf_game_context.py` to stamp cols on live rows
3. Monitor slate — if lift < v1.05, may want to A/B test v1.05 vs v1.06 for a week

## Original 2026-09-14 memo below

**Fact:** NCAAF `primary_play._lr_total_shadow.p_over` values across the 9/12 82-game slate ranged only [0.4578, 0.5500] — 82/82 within ±5.5pp of 0.5. Median = 0.5013 (exactly coin-flip). No game got a real over/under lean from this signal.

**Discovered 2026-09-14 morning audit** (via subagent + follow-up diag).

**Fact:** NCAAF `primary_play._lr_total_shadow.p_over` values across the 9/12 82-game slate ranged only [0.4578, 0.5500] — 82/82 within ±5.5pp of 0.5. Median = 0.5013 (exactly coin-flip). No game got a real over/under lean from this signal.

**Root cause:** Model at `mlb_pipeline/models/ncaaf_total_logreg.json` is trained on 6 features, ALL market lines:
- close_total, open_total (highly correlated with each other)
- close_spread, open_spread (highly correlated with each other)
- close_home_ml, close_away_ml

No team/game context features (offense EPA, defense EPA, pace, temp, wind, dome, SP+, returning production, etc.). Market lines are already priced-in; without a non-market signal, LR can only find tiny inconsistencies between correlated columns. Meta from model:
- test_accuracy: 51.77%
- baseline_accuracy: 50.30%
- lift_pp: 1.47pp
- n_train: 4285

The model IS the market. Lift is barely detectable.

**Why it matters:** `apply_ncaaf_total_lr_override` in `defensive_gates.py` runs in demote-only mode and only fires on coin-flip verdicts (LR says "no side, kill the pick"). Because p_over is stuck near 0.5 on every game, it triggers coin-flip demotion RARELY — the signal is inert. No harm done, but no value added either.

**How to apply:**

1. **Short-term (no action needed):** LR total for NCAAF is a no-op signal today. Do not use it as a scoring source in the ensemble. LR-warn cap uses `_lr_ml_shadow` (ML), not total — that gate is still valuable.
2. **v1.1 retrain (queued):** Rebuild `ncaaf_total_logreg_train.py` with team features:
   - Rolling offense EPA (home + away, L4/L8)
   - Rolling defense EPA (home + away)
   - Pace (plays/game)
   - Weather: temp, wind, dome flag
   - SP+ overall (home + away) — already in ctx
   - Returning production (home + away)
   - Conference / neutral site flag
3. **Alternative:** Use `sp_plus_pred_total` from ctx as the primary NCAAF total signal — already populated, already validated by SP+ methodology.

**Related:**
- [[project_lr_totals_investigation_908]] — earlier LR totals investigation (NCAAF shadow-only; NFL total missing)
- Yesterday's model scorecard: LR_TOTAL 0-2 sample too small to matter, but the p_over stuck near 0.5 is why it never picks a side.
