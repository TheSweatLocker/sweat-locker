---
name: feedback-fade-not-suppress-803
description: "Don't suppress \"known bad\" props — flip direction and publish as FADE signal (market priced it in)"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-04T00:35:49.097Z
---

**Rule**: When a (prop_type, tier, direction) bucket has historical hit rate below 45%, do NOT suppress. FADE the other side. A 29% W bucket is a 71% FADE signal — the market has priced it wrong in that direction, we take the opposite.

**Why**: 2026-08-03 user directive: "I don't want to suppress garbage if we know its bad. Jerry should say fade back the other side, market has priced it in." Suppression throws away the signal. Fading turns the loser into an edge play.

**Implementation** ([[project_card_composition_audit_803]] audit data):
- Encoded in `prop_tier_calibration.py::FADE_COMBOS` map
- Each entry: (prop_type_family, tier, direction) → (fade_tier, inverse_pct, n)
- When `apply_calibration()` sees a fade combo, it flips direction, updates prop_type suffix, and sets tier based on inverse hit rate:
  - Inverse >=70% → STRONG conv 78
  - Inverse 60-69% → LEAN conv 65
  - Inverse 55-59% → LEAN conv 55

**Current fade combos (as of 2026-08-03)**:
- outs_over SKIP over 29% → FADE to outs_under STRONG (n=51)
- er_under SKIP under 24% → FADE to er_over STRONG (n=29)
- ha_over LEAN over 33% → FADE to ha_under STRONG (n=27)
- ha_over SKIP over 38% → FADE to ha_under LEAN (n=24)
- bb_under SKIP under 40% → FADE to bb_over LEAN (n=25)
- er_under STRONG under 40% → FADE to er_over LEAN (n=20)
- er_over STRONG over 44% → FADE to er_under LEAN (n=41)

**How to apply**:
- Any future audit that surfaces a bucket at <45% hit rate → add to FADE_COMBOS, NOT to a suppress list
- Recompute fade table quarterly (or when compute_prop_bucket_roi surfaces new losers)
- Jerry's synthesis reads the flipped direction naturally — his BACK/FADE/PASS logic works on the direction we hand him

**What we DON'T fade**:
- Combos with n < 15 (variance, not signal)
- Combos at 45-55% (too soft to earn a directional edge)
- Combos where flip direction has no book_odds populated (batter hits gap)

Related: [[project_card_composition_audit_803]] (parent audit that found the leaks), [[feedback_batter_hits_juice_trap_803]], [[feedback_heavy_fav_ml_trap_803]].
