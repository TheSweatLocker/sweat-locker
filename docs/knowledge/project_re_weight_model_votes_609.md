---
name: project-re-weight-model-votes-609
description: "Jerry totals bands halved 2026-06-09 (26/20/14/8 → 13/10/7/4) to match its 50% lifetime baseline vs v3's 66.8%"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Change shipped 2026-06-09 (play_of_day.py around line 1175):** Jerry total contribution bands halved when confirmed by v3 or v4. Was 26/20/14/8 max points; now 13/10/7/4.

**Why:** `cohort_signals.play_baselines` field literally encodes which model to trust at face value — that's the data we already have, just needed to respect it. Numbers:
- v3_tot: 66.8% direction accuracy lifetime (real edge — 16.8pt above coin flip)
- v4_tot: 56.0% (modest — 6pt above)
- jerry_tot: 50.0% (literal coin flip, no standalone edge)

The 2026-06-05 1.5x Jerry boost was driven by an n=8 backtest sweep that showed 7-1 / 87.5% on confirmed Jerry. Sample size was too small — the boost made Jerry the LOUDEST single contributor to TOTAL dim (26 max vs v3's 18), contradicting what the live baselines say. 2026-06-09 audit of 15 Jerry reads found 5 had "HIGH conviction" language directly driven by overweighted Jerry totals (NYY@CLE, BOS@TB, CIN@SD, MIL@ATH, ARI@MIA — all had models split but Jerry over-contributed to push score over the STRONG threshold).

**Dry-run on 6/9 slate (n=15):**
- 3 tier-downs (all Jerry-driven overconfidence): SEA@BAL LIGHT→PASS, HOU@LAA STRONG→LIGHT, MIL@ATH STRONG→LIGHT
- PRIME games unchanged (LAD, WAS@SF, STL@NYM) because their conviction comes from v3+v4 consensus + cohorts, not Jerry alone

**How to apply:**
- Watch tomorrow morning's slate — should see fewer false STRONG total tiers when Jerry was the standalone driver
- Validation criteria: STRONG total tier hit rate should improve materially
- Related: [[project_model_architecture_xgboost_role]], [[feedback_let_engine_speak]]
- Do NOT also halve Jerry SIDE (already benched 2026-06-05 — Jerry spread 47.8% lifetime)
- For ML/RL: v3 and v4 baselines are within 3pt of each other (no rework needed); Jerry ML 53.4% baseline marginal — current weighting OK

**Still pending:**
- v4 standalone TOTAL contribution (currently only enters via consensus bonus and Jerry confirmation). Could surface as its own band but baseline is only 56% — needs design discussion before shipping.
- Conf_ml: 50% baseline (literal coin flip) but still flows into confluence_net calculation. Consider whether to discount its vote.
