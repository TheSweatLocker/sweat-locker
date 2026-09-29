---
name: Total-result single-factor cohorts shipped (2026-05-10)
description: Audit cohorts that bucket games by wRC+/xERA/BP/recency and measure OVER hit rate. Found 64% OVER when both pens >4.5 ERA, 56% OVER when combined wRC+ ≤184 (counter-intuitive), 46% OVER (UNDER lean) when both teams hot L5.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Shipped four single-factor total-result cohorts in `audit_tier_calibration.py` on 2026-05-10. Compute OVER hit rate conditioned on each factor.

**Results on 2,400+ historical games (STD window):**

**Strongest signal — Bullpen quality (avg of both pens' ERA):**
- `total_over_bp_era_high` (>4.5): **64.3% OVER (n=56)** ⭐ — strongest single-factor edge
- `total_over_bp_era_low` (≤3.5): 46.4%
- `total_over_bp_era_mid` (3.5-4.5): 46.7%

**Starter xERA quality (avg of both starters' xERA):**
- `total_over_starter_xera_high` (>4.5): **59.8% OVER (n=107)**
- `total_over_starter_xera_low` (≤3.5): 53.2%
- `total_over_starter_xera_mid`: 47.7%

**Combined wRC+ (counter-intuitive):**
- `total_over_wrc_combined_low` (≤184): **56.0% OVER (n=100)** — markets shade two-bad-offense games too far down
- `total_over_wrc_combined_high` (≥215): 52.0%
- `total_over_wrc_combined_mid`: 49.3%

**Recency drift (combined L5 run diff):**
- `total_over_l5_combined_hot` (sum ≥+1.0): **45.9% (UNDER lean) (n=207)** — markets overcorrect on hot teams
- `total_over_l5_combined_cold` (sum ≤-1.0): 53.3% (n=212)

**Why:** User asked if we could correlate game total result with multi-factor offense+pitching+pen+recency data. mlb_game_results already had all the feature columns needed (140 cols total) — no schema change required. Same join pattern as K-Over correlation cohorts.

**Field naming gotcha:** pipeline uses `home_sp_xera` not `home_sp_era` (the latter is null on all resolved games). Original cohort definition used `_era` and showed all zeros until renamed to `_xera`.

**How to apply:** When tonight's game matches multiple cohort signals in the same direction, conviction stacks. Astros@Reds 5/10 had both pens >4.5 ERA (HOU 5.56, CIN 4.63) — bp_high cohort 64% OVER aligned with DAWG ML edge → multi-factor OVER stack. POTD Braves@Dodgers UNDER 9.0 supported by l5_hot fade + low pen ERA cohorts.

**Key strategic insight:** The 64% bp_era_high signal is publishable content Oddible (the breadth competitor) cannot match. Audit-validated single-factor cohort = differentiator.

**Engineering location:** `mlb_pipeline/audit_tier_calibration.py` — `fetch_resolved_with_total_features()`, `compute_total_factor_window_rates()`, wired into `main()` between K-Over correlation and NBA cohorts.

**Followup work queue:**
1. Build two-factor combo cohorts (e.g., `bp_high AND wrc_low`) once individual buckets have ≥80 samples
2. Add hot-fade tier to pipeline as actual conviction lean (not just audit cohort)
3. Direction 2 (similar-offense projections for ER + Outs props) is the next data depth scope
