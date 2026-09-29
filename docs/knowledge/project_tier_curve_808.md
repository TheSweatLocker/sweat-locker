---
name: project-tier-curve-808
description: 30d MLB tier hit-rate curve non-monotonic; conviction dead zones at 60-64 and 75-79; total STRONGs (83%) crushing ml STRONGs (57%)
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-08T17:21:04.615Z
---

30-day audit of jerry_reads (2026-07-08 → 2026-08-07, n=115 picks, 84 graded).

**Overall hit rates by tier:**
- PRIME: 100% (n=2 — too small)
- STRONG: 63.8% (n=47)
- LEAN: 61.5% (n=39)
- READ: 0% (n=2 — too small)

**BIGGEST FINDING — Market × Tier gap:**
- **total STRONG: 83.3% (n=12)** ← Jerry's totals STRONG are elite
- ml LEAN: 63.2% (n=19)
- total LEAN: 60.0% (n=20)
- **ml STRONG: 57.1% (n=35)** ← 26pp gap vs total STRONG

Jerry's totals conviction is way more predictive than his ML conviction.
This suggests ml tier gating is too loose — many "STRONG ML" picks are
actually behaving like coinflips.

**Conviction dead zones (non-monotonic curve):**
- 55-59: 70.6% (n=17) 🟢 BEST BUCKET
- 65-69: 66.7% (n=18)
- 70-74: 68.2% (n=22)
- **60-64: 47.1% (n=17)** 🔴 DEAD ZONE
- **75-79: 42.9% (n=7)** 🔴 DEAD ZONE (high STRONG)
- 50-54: 80% (n=5, small)

Higher conviction does not linearly = higher hit rate. The 55-59 LEAN
bucket outperforms the 75-79 high-STRONG bucket by 27pp.

**Why:** Card composition audits (see project_card_composition_audit_803)
have flagged this pattern before. The 60-64 band is where "borderline
tier" games live — signals not strong enough for STRONG but too loud
to demote to LEAN. The 75-79 band is where "hand-tuned PRIME" leftovers
sit that didn't quite clear 80.

**How to apply:**
- Card selection: avoid stacking picks from 60-64 or 75-79 bands as
  anchors unless multi-lens confluence explicitly confirms
- Prompt tuning: consider adding "avoid overclaiming to 60-64 or 75-79"
  guidance to Jerry
- Investigation queue: is there a specific market or context type that
  concentrates in the dead zones?
- Do NOT retune tier thresholds yet — n=17 is real but not overwhelming
  for a threshold change. Collect 50+ samples per bucket first.

**Immediate card-composition rule:**
For sweat card ANCHOR (top pick), prefer 65-74 or 55-59 conviction over
60-64 or 75-79 picks even if they share the same tier label.

**Linked:** [[project_card_composition_audit_803]], [[project_ml_rl_and_new_tiers_727]]
