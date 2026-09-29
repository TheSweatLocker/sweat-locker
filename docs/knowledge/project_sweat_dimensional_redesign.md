---
name: sweat-dimensional-redesign
description: "Sweat score split into side/total/prop sub-dimensions 5/29; headline = max sub-score; winning dimension drives \"model likes X\". Audit through end of June, freeze model thereafter."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Sweat score rewritten 2026-05-29 evening to split into three dimensions: **side** (ML/spread/RL), **total** (Over/Under/NRFI/YRFI), **prop** (game has standout props). Each dimension scored independently with the same 80/65/50/<50 tier cutoffs. Headline `sweat_score` = `max(side, total, prop)`; `sweat_tier` derives from the winning dimension's tier.

**Trigger:** 5/29 ATL/CIN — model_total +0.93, GABP park 108, 4 PRIME/STRONG props all pointing Over, but old single-bucket scorer landed it at PASS 38 because zero side edge washed everything. New scorer: TOTAL 55 LIGHT_LEAN with "Over 9.5" as the model_play headline.

**Files:** [`mlb_pipeline/play_of_day.py`](mlb_pipeline/play_of_day.py) — `score_mlb_game()` returns `(headline_score, dimensions)`; `_compute_prop_alignment()` is the new aligned-prop direction helper; `write_sweat_score()` cap logic now dimension-aware.

**Schema:** Dimensions persisted in `mlb_game_context.sweat_breakdown` JSONB under key `dimensions` alongside legacy `contributions`/`evidence`. No new column. Structure:
```
sweat_breakdown.dimensions = {
  side:  {score, tier, drivers[], play{}},
  total: {score, tier, drivers[], play{}},
  prop:  {score, tier, drivers[], play{}},
  winning_dimension: 'side'|'total'|'prop',
  model_play: {...}  # winning dim's play
}
```

**Key reband decisions:**
- Total delta: was max +9, now +18 (matches spread-delta weighting). Old bands let total-only edges hide.
- Park run factor: added 105/95 mid-tier at +3 — old binary 110/+4 missed soft leans like ATL/CIN at 108.
- xERA gap moved from SIDE to TOTAL — a 2-run pitcher gap is a total signal (one side scores, one doesn't), not a side signal.
- NEW: aligned-prop direction (+18/+14/+6 at 4/3/2 aligned PRIME+STRONG props). ATL/CIN's 3 OVER props finally counted.
- PRIME `primary_play` floor: when pipeline endorses a play as PRIME, the relevant dimension floors at 80. Fixes the NYY/ATH stall where `spread_delta` column was 0 due to known sign-convention bug but the actual edge was +2.5.

**Tier gate per dimension:** PRIME requires that dimension's `play` to be populated (actionability gate). Without a play, score caps at STRONG. Replaces the old global `ctx.primary_play` gate which fired on total-only edges and capped them incorrectly.

**Audit plan (user-set 5/29):** Run dual-scoring for ~4 weeks. Compare PRIME/STRONG hit rates per dimension. Watch for:
- TOTAL PRIME hitting cleanly (audit-validated, not noisy)
- Aligned-prop-direction signal hit rate (new signal, no history)
- Side PRIME floor catching real cases vs over-firing
- Distribution shape: aim for ~1-3 PRIME, ~3-6 STRONG, ~4-7 LIGHT_LEAN, ~1-3 PASS per slate

**Freeze deadline:** End of June 2026 — model should be set in its ways by then. Major scoring changes after that point require strong audit justification only.

**5/29 dry-run results vs tonight's published sweat_scores:**
- MIA/NYM POTD: 79 → **98 PRIME** TOTAL (NRFI) ✓
- NYY/ATH: 66 → **80 PRIME** SIDE (Yankees ML) ✓
- ATL/CIN: 38 PASS → **55 LIGHT_LEAN** TOTAL (Over 9.5) ✓
- SF/COL: 79 → 70 STRONG TOTAL (model_play missing — `projected_total` data gap)

Related: [[sweat-score-rewrite]] (5/16 single-bucket rewrite — superseded by this), [[v4-over-drift]] (why we prefer NRFI band over total_delta for MIA/NYM — v4 OVER drift bleeds into v3 projected_total too), [[spread-delta-trap-zone]] (sign-convention bug context for NYY/ATH).
