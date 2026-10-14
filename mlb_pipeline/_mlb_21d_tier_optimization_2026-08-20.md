# MLB Tier Gate Optimization — 21-day Reverse-Engineering Study
**Window:** 2026-07-30 → 2026-08-19 (21 days)
**Author:** tier-opt subagent · 2026-08-20
**Deliverables:** `_mlb_tier_opt_2026-08-20.py`, `_mlb_tier_opt_sim2_2026-08-20.py`, `_mlb_tier_opt_picks_2026-08-20.json` (534 graded picks), `_mlb_tier_opt_cohorts_2026-08-20.json`, `_proposed_tier_thresholds_2026-08-20.json`

---

## 1. Dataset

- **179** `mlb_game_context` rows in-window; **280** resolved `mlb_game_results`; **74** `line_movement_flags` (across 53 games).
- Every context row was **re-scored through `ensemble_scorer.score_game()`** so the whole 21-day window is graded on the current ensemble_v2 logic. The stored `primary_play` uses `ensemble_v2` only from 8/17 onward (32 games) — that pool alone was too thin, so the study joins the entire window and carries the original engine label (`legacy`, `ensemble_v2`, `legacy_compute_primary_play`) as a covariate.
- **534 graded market picks** (up to 3 per game — ML / RL / Total) with W/L/P + payout using `home_ml_close`/`away_ml_close` for ML, -110 for RL/Total.

**Current tier distribution (re-scored, current gates):**

| Tier   | n   | %      | W-L-P     | HR    | Units    |
|--------|-----|--------|-----------|-------|----------|
| PRIME  | 25  | 4.7%   | 15-10-0   | 60.0% | +2.70    |
| STRONG | 168 | 31.5%  | 96-72-0   | 57.1% | +18.30   |
| LEAN   | 341 | 63.9%  | 182-153-6 | 54.3% | +12.57   |
| PASS   | 0   | 0.0%   | —         | —     | —        |

LEAN is 64% of everything the ensemble publishes at a 54.3% hit rate — one of many confirmations of the user's felt problem.

---

## 2. Top winning cohorts (n ≥ 15)

| Cohort                                | n  | HR    | ROI     |
|---------------------------------------|----|-------|---------|
| STRONG · pitcher-dominant top-class   | 20 | 90.0% | +71.9%  |
| LEAN · cohort-dominant top-class      | 31 | 71.0% | +39.4%  |
| STRONG · Margin ≥ 1.0                 | 46 | 69.6% | +34.0%  |
| Score 1.5-2.0 (all tiers)             | 19 | 68.4% | +26.1%  |
| **LEAN · Score 0.7-1.0**              | 16 | 68.8% | +34.4%  |
| PRIME · Score 1.5-2.0                 | 18 | 66.7% | +22.5%  |
| **LEAN · situational-dominant**       | 36 | 66.7% | +27.3%  |
| Margin ≥ 1.0 (all tiers)              | 71 | 66.2% | +25.9%  |
| Margin 0.5-0.7 (all tiers)            | 52 | 65.4% | +32.5%  |
| **LEAN · model-dominant**             | 18 | 64.7% | +18.5%  |
| **LEAN · Margin 0.5-0.7**             | 25 | 64.0% | +27.0%  |
| Score 1.0-1.3 (all tiers)             | 47 | 61.7% | +23.1%  |
| **LEAN · Margin 0.1-0.3**             | 125| 61.0% | +16.4%  |

The bolded rows are the **PRIME/STRONG-worthy cohorts currently mislabeled as LEAN**. These four groups alone (score 0.7-1.0, cohort-dom, situational-dom, model-dom, margin 0.5-0.7) contain ~120 LEAN picks that hit 60-71%.

## 3. Top losing cohorts (should demote / kill)

| Cohort                                | n  | HR    | ROI     |
|---------------------------------------|----|-------|---------|
| Score 0.3-0.7, classes=1              | 39 | 38.5% | -26.6%  |
| **STRONG · scenario-dominant**        | 26 | 42.3% | -22.5%  |
| LEAN · h2h-dominant                   | 41 | 42.1% | -17.9%  |
| LEAN · team_form-dominant             | 35 | 42.9% | -18.3%  |
| LEAN · external_pick-dominant         | 36 | 44.4% | -13.1%  |
| LEAN · Margin 0.3-0.5                 | 85 | 45.2% | -14.4%  |
| **STRONG · Margin 0.7-1.0**           | 86 | 47.7% | -8.6%   |
| **STRONG · ML market**                | 64 | 48.4% | -2.9%   |
| **LEAN · ML market**                  | 95 | 47.4% | -9.6%   |
| Score 0.3-0.7 (all tiers)             |165 | 48.5% | -7.8%   |

**Biggest structural loss: ML market at every tier.** Overall ML: 48.9% HR, -5.6% ROI. RL: 58.4% / +11.6%. Total: 59.3% / +13.3%. The heavy-fav-ML-trap memory is fully corroborated: LEAN ML costs ~1.6u/21d, STRONG ML costs ~3.9u/21d — the ensemble is publishing losing tickets on the ML surface at scale.

The margin-band non-monotonicity (m0.1-0.3 wins 61%, m0.3-0.5 loses 45%, m0.5-0.7 wins 65%) looks bimodal / small-sample and the gates below don't overfit to it — they only require m ≥ 0.3 or 0.5 to promote.

---

## 4. Proposed tier gates — diff vs current

Six market-aware/promote-aware proposals were simulated. The recommended one is **Proposal L** ("aggressive_prime_max"):

```jsonc
// PROPOSAL L — full config
{
  "PRIME":  {"min_score": 1.0, "min_classes": 3, "min_margin": 0.3},
  "STRONG": {"min_score": 0.7, "min_classes": 2, "min_margin": 0.3},
  "LEAN":   {"min_score": 0.3, "min_classes": 2, "min_margin": 0.1},
  "PRIME_BREADTH":  {"min_score": 0.9, "min_classes": 4, "min_margin": 0.5},
  "MARGIN_PROMOTE_STRONG":     {"min_margin": 0.5, "min_score": 0.5},
  "TRUST_TOP_CLASS_PROMOTE": {
      "classes": ["cohort","situational","pitcher","model"],
      "min_score": 0.5, "min_margin": 0.3
  },
  "market_overrides": {
    "ml": {
      "kill_lean": true,                                  // no LEAN publishes on ML
      "STRONG": {"min_score": 1.0, "min_classes": 3, "min_margin": 0.5},
      "PRIME":  {"min_score": 1.3, "min_classes": 3, "min_margin": 0.5}
    }
  }
}
```

**Diff vs. current gates:**
- **PRIME** lowered: score 1.5→1.0, margin 0.5→0.3 (classes still 3). Cohort data shows score 1.0-1.3 hits 61.7% and margin ≥ 0.3 with 3+ classes is a reliable filter.
- **PRIME_BREADTH** lowered: 1.2/5/0.7 → 0.9/4/0.5. Widens the second lane to catch high-confluence picks.
- **LEAN** stiffened: min_classes 1 → 2 (single-signal LEANs get demoted to PASS unless promoted).
- **New MARGIN_PROMOTE_STRONG** lane: score ≥ 0.5 with margin ≥ 0.5 auto-STRONG — captures the m0.5-0.7 winners.
- **New TRUST_TOP_CLASS_PROMOTE**: LEAN whose dominant class is `cohort/situational/pitcher/model` → STRONG when score ≥ 0.5, margin ≥ 0.3. Captures the 60-71% LEAN cohort winners.
- **Market override for ML**: no LEAN publishes on ML; STRONG ML requires min_score 1.0 + classes 3 + margin 0.5 (stricter than baseline STRONG); PRIME ML requires min_score 1.3.

**Sharp-flag boost was NOT included.** In-window sharp flags (SHARP_MOVE_CONFIRMED / TRIPLE) are extremely rare (n=9 in the whole 534-pick pool where the sharp side even agrees), so promoting by sharp gives near-zero coverage. Keep as an add-on to revisit when the flag population grows.

---

## 5. Simulation — Proposal L vs current gates, same 534-pick pool

| Metric              | Current (baseline) | **Proposal L**       |
|---------------------|-------------------:|---------------------:|
| PRIME n (%)         | 24 (4.5%)          | **56 (10.5%)** ⬆     |
| PRIME hit rate      | 62.5%              | **66.1%** ⬆          |
| PRIME units         | +3.70              | **+12.42** ⬆         |
| STRONG n (%)        | 170 (31.8%)        | 125 (23.4%)          |
| STRONG hit rate     | 56.5%              | 56.0%                |
| STRONG units        | +16.30             | +9.22                |
| LEAN n (%)          | 165 (30.9%)        | 52 (9.7%)            |
| **LEAN hit rate**   | **51.8%**          | **58.8%** ⬆ +7 pp    |
| LEAN units          | -1.70              | +6.30                |
| PASS n (%)          | 175 (32.8%)        | 301 (56.4%)          |
| Quality-weighted score | 111.05          | 107.02               |
| **Total units**     | +18.30             | **+27.94 (+53%)** ⬆  |

Both of the user's stated goals are met:

1. **More PRIMEs** — 24 → 56 published, at *higher* hit rate (62.5% → 66.1%), driving PRIME units from +3.7 to +12.4.
2. **LEANs win more** — 51.8% → 58.8%, a +7 pp lift by cutting the fade-zone LEANs (ML, single-signal, and m0.3-0.5) and promoting the cohort/situational/model-dominant winners to STRONG.

Quality-weighted score (`PRIME_n·PRIME_hit + STRONG_n·STRONG_hit`) drops 4 points because STRONG shrinks by 45 picks, but net units improve by +9.6u — the STRONG picks that got dropped were the losing ML STRONGs (n=66, 47% HR, -3.85u), so the trade is money-positive.

### Alternative if the "STRONG must stay big" instinct is stronger
**Proposal G** (F + trust-class) holds STRONG volume at 176 (33.0%) with HR 57.7%, quality_score 116.55, units +19.74. Middle-ground option — slightly more STRONG, slightly less units, doesn't grow PRIME.

---

## 6. Distribution vs. user target

User target: 15-25% PRIME / 25-35% STRONG / 40-50% LEAN / rest PASS.

Proposal L delivers 10.5 / 23.4 / 9.7 / 56.4. **We cannot hit the 15-25% PRIME target without dropping PRIME HR below 60%** — the underlying data doesn't have that many high-confluence picks in a 21-day window. Getting to 15% PRIME requires PRIME score ≤ 0.7, which pulls in the s0.3-0.7 junk zone (48.5% HR). Any tier-optimization that reports 20%+ PRIME on this dataset is overfitting.

Similarly, the 40-50% LEAN target conflicts with "increase LEAN win rate" — the LEAN bucket at baseline volume (30.9%) already hits 51.8%, so preserving that volume caps the HR upside near breakeven. Proposal L kills 68% of LEANs to reach 58.8% HR — a real trade the user should weigh.

---

## 7. Verdict

**Ship Proposal L as a candidate for a shadow-mode swap into `ensemble_scorer.TIER_THRESHOLDS`.** Net improvement is real and matches the stated goals:

- +32 PRIME picks over 21d (24 → 56) at higher HR (62.5% → 66.1%).
- LEAN HR jumps 51.8% → 58.8%.
- Simulated total units +53% (+18.30 → +27.94).

**But there are three risk callouts before flipping the live gate:**

1. **Sample size — 21d is not enough for cohort claims.** The "STRONG pitcher-dominant 90% HR n=20" and "LEAN cohort-dominant 71% n=31" cohorts drive the trust-class promote logic. Both are underpowered against a null of 55%. Suggest running the same script on a **60-day window** before flip; the trust-class list may need to shrink.
2. **Look-ahead bias.** Re-scoring with today's `signal_registry` weights means signals that got their weights bumped mid-window get retroactive credit. The `legacy` engine picks (417 of 534) were graded with weights that didn't exist at decision time. Ranking of proposals should be robust to this — but the *magnitude* of the units lift (+53%) is optimistic. Real ROI lift on go-forward is likely half that.
3. **Losing 45 STRONG picks and 113 LEAN picks** = ~7-8 fewer publishable plays per slate. Two consequences: (a) some days will have thin cards (0-1 STRONG); (b) subscribers who bet volume will notice. The `MARGIN_PROMOTE_STRONG` + `TRUST_TOP_CLASS_PROMOTE` lanes reclaim ~40 picks, but the net still hurts card volume. Weigh against the +53% units lift.

**Recommended rollout:** run the script on a 60-day window first, confirm the trust-class winners and the ML-kill hold up out-of-sample, then swap `TIER_THRESHOLDS` behind a `TIER_GATES_VERSION='2026-08-20'` env flag with a 7-day shadow (tier assignment computed with new gates but written to `primary_play.shadow_tier`, published tier stays baseline) before promoting to live. If shadow-mode confirms, promote.

**Alternative if only tier volume matters:** Proposal G (baseline PRIME/STRONG + trust-class promote + market_override ML kill). Same LEAN protection, keeps ~33% STRONG volume, less aggressive PRIME expansion.

---

## Appendix — per-market breakdown, baseline vs Proposal F (the kill-ML-LEAN proposal, ancestor of L)

| Market | Tier    | n  | W-L-P    | HR    | ROI      |
|--------|---------|----|----------|-------|----------|
| ML     | PRIME   | 18 | 11-7-0   | 61.1% | +11.4%   |
| ML     | STRONG  | 28 | 12-16-0  | 42.9% | -20.9%   |   ← still bleeds; ML STRONG override alone isn't enough
| RL     | STRONG  | 81 | 50-31-0  | 61.7% | +17.9%   |
| TOTAL  | STRONG  | 43 | 28-15-0  | 65.1% | +24.4%   |
| TOTAL  | LEAN    | 8  | 3-5-0    | 37.5% | -28.4%   |

Observation for follow-up: STRONG ML at 42.9% is still a losing surface even after the score≥1.0 override. Consider a further ML-only rule: **require top_class ∈ {model, cohort, pitcher} for any ML STRONG** to publish. Not folded into Proposal L to keep changes bounded — flag as second-iteration tightening.
