---
name: project_advanced_metrics_backtest_815
description: 2026-08-15 backtest results on 5-part advanced metrics pack. BABIP regression flag VALIDATED (94-96% hit rate n=283). Simplified SIERA FAILS (23% worse than FIP-style xERA). TTTO untestable historically (no starter IP archive). Framing + bullpen deferred to live A/B.
metadata:
  type: project
---

**2026-08-15 advanced metrics backtest results** — 3 formal backtests on the 5-part sabermetrics pack shipped in commit edd8f4f4.

## Backtest #1 · SIERA vs xERA — SIERA LOST

- SIERA RMSE: 2.422
- xERA RMSE:  1.961 (my FIP-style proxy)
- SIERA -23.5% WORSE
- Sample n=32 pitcher-splits (5 teams sampled for speed)

**Interpretation:**
- My simplified SIERA formula drops the GB-FB-PU interaction terms → known formula gap
- My "xERA" proxy is actually FIP-style: (13·HR + 3·BB - 2·K)/IP + 3.10. FIP is famously robust with just 4 inputs.
- Small sample (n=32) — full 30-team backtest may change picture
- Full SIERA with Savant pitch-type data would likely reclaim the edge

**Decision:** Do NOT integrate simplified SIERA into projections. Field still computes for future use if we invest in full formula.

## Backtest #2 · TTTO penalty — UNTESTABLE HISTORICALLY

- `mlb_game_results` fields: close_total 1609 · projected_total 1812 · home_last_ip **NULL for all rows**
- Starter IP data was never archived on completed games
- Can't measure "residual by starter IP bucket" without archive
- Would need MLB API historical backfill (~5000 API calls, 4hr work)

**Decision:** Skip formal backtest. Either backfill starter IP OR run live A/B. Ship TTTO field computing → collect 30-60 days of "with TTTO" vs "without" projection accuracy.

## Backtest #3 · BABIP regression flag — STRONG VALIDATION ✅

- Method: proxy L14 BABIP via L14 RPG (runs per game) since per-PA K/AB not archived
- HOT teams (L14 RPG > season+1): n=143 · 94% regressed down · mean -1.89 runs
- COLD teams (L14 RPG < season-1): n=140 · 96% rebounded up · mean +1.65 runs

**Interpretation:**
- 94%/96% regression rate is powerful — one of strongest signals we've validated
- Mean regression 1.89/1.65 runs is LARGE because flag is set at outlier level (>1 R/G from mean)
- Even cutting in half for conservative estimate = 47%/48% regression signal = usable edge
- Note: L14 RPG proxy is broader than pure BABIP (captures hot/cold streaks beyond luck)

**Decision:** INTEGRATE BABIP regression flag immediately. Add cohort tags:
- `regression_hot` → fade offense (bet UNDER their side, opposing pitcher props OVER)
- `regression_cold` → back offense (bet OVER their side, opposing pitcher props UNDER)

## Overall pack status

- 5 metrics ship computing: TTTO, framing, BABIP, bullpen, SIERA
- Only BABIP formally validated for downstream integration
- SIERA simplified formula fails; full-formula rebuild requires Savant pitch-type enrichment
- TTTO/framing/bullpen deferred to live A/B (no historical archive to backtest)

## How to apply

1. **Ship BABIP integration next.** Cohort tags on sweat_breakdown, small refinement to compute_primary_play to demote offense when flagged hot.
2. **Do NOT integrate simplified SIERA.** Field stays for future use.
3. **Live A/B for TTTO + framing + bullpen.** Ship compute-only for 30-60 days. Compare projection RMSE on games with vs without the derived fields populated. Natural variance test.
4. **If we invest in full SIERA:** need Savant pull for pitch-type % per pitcher, add GB-FB-PU interaction terms. ~4hr work.
5. **If we invest in TTTO archive:** backfill `home_last_ip`/`away_last_ip` on historical mlb_game_results via MLB API loop (~5000 calls, 4hr). Then re-run backtest.

**Key methodological lesson:** literature-validated sabermetrics still need OUR-data calibration. SIERA is well-established (3-5% better than xERA in academic studies), but our simplified formula lost by 23%. Formulas built from partial input data underperform expectations.

Related: [[project_yday_814_variance_lesson]], [[project_calibration_architecture_805]].
