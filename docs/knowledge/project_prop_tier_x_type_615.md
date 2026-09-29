---
name: project_prop_tier_x_type_615
description: Prop hit rates vary DRAMATICALLY by (tier × prop_type) combo. PRIME bb_under = 53% (coinflip) while outs_under any tier = 96% (massive edge). Tier alone is misleading; type+tier together is the real signal.
metadata:
  type: project
---

Analysis of 1,213 graded props over 30 days reveals huge variance by prop type within the same tier. Treating "PRIME" as automatic confidence misses that the type matters more.

**Real edges (>60% across tiers):**
- **outs_under: 96% (n=25)** — pitchers getting pulled early. PRIME 100%, STRONG 92%, LEAN 100%
- **hits_over: 70% (n=319)** — biggest sample, real edge
- **er_over: 68% (n=34)**
- **ks_under: 65% (n=74)** — STRONG ks_under hits 70% on n=47
- **ks_over: 64% (n=45)**
- **bb_under: 60% (n=73)** but tier-dependent (see below)

**Calibrated (~50-58%):**
- ha_over: 57% | bb_over: 56% | hits_under: 56% | ha_under: 52%

**LOSING:**
- **outs_over: 40% (n=15)** — hard fade

**Top tier × type combos to chase:**
- outs_under (any tier): 96%
- STRONG bb_under: 78% (PRIME bb_under is only 53%)
- PRIME er_under: 75% | PRIME ha_over: 75%
- STRONG ks_under: 70%
- STRONG hits_over: 69% | PRIME hits_over: 68%

**Coin-flip combos to avoid posting as "PRIME locks":**
- PRIME bb_under: 53% (n=32) ← Wheeler 6/15 loss was this combo
- STRONG bb_over: 55% (n=38) ← Spence 6/15 loss was this combo
- ha_under any tier: 52%

**Why:** Conviction score within the prop pipeline weights features (recency, matchup, season form) that drive tier classification. But prop type has its own base rate that the tier doesn't capture. A "PRIME PRIME-tier bb_under" doesn't mean 70% hit rate — it means 53% historical hit rate when this scorer has highest conviction on a BB Under bet.

**How to apply:**
- When recommending props, surface (tier × prop_type) hit rate, not just tier
- Filter out coinflip combos (<55%) from publishable picks regardless of conviction
- Aggressively pursue outs_under bets at any tier
- De-weight bb_under at PRIME (tier inflation problem)

Related: [[project_live_track_record_614]] [[feedback_user_doubt_is_signal]]
