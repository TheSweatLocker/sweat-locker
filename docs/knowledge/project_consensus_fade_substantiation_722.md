---
name: consensus-fade-substantiation-722
description: v2 fade detector requires per-bucket audit substantiation. UPDATED 7/27 — 30d/7d data: universal chalk WINS 60-65%, not loses. Fade hypothesis rejected without corroborating model contra.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-27T21:17:01.693Z
---

**Set 2026-07-22 evening. Substantially UPDATED 2026-07-27 with 30d + 7d data.**

## Design decision (unchanged)

**v1 (7/22 morning, deprecated):** fired ⚠️ FADE on ANY 75%+ consensus.
Based on ONE data point (7/21 aggregate 53% n=13). Not substantiated.

**v2 (7/22 evening, live):** REQUIRES per-bucket audit substantiation.
Buckets by (sport, surface, pct_band, model_alignment). Only fires FADE
when the specific bucket hits <48% at n>=20. Otherwise MONITORING (yellow
chip — "we see it, tracking, not calling it").

## RESOLVED 2026-07-27: Universal chalk wins, don't fade blindly

**7d data (7/20-7/26, n=30 heavy-consensus games):**
- Heavy consensus (75%+ books one side): **public won 18/30 = 60%**
- Unanimous consensus (100%): **public won 11/17 = 65%**

**30d holds the same shape** (verified via pattern_analysis.py 7/27).

**Andy's original read on 7/22 ("100% chalk = fade") is REJECTED by data.**
Universal external consensus is a POSITIVE signal, not fade. Public going
unanimous at 65% hit rate means public is finding real edges (or at least
tracking Vegas/market well enough to be right).

**When fade IS the right call:** contra alignment — public heavy on one
side while OUR MODEL is on the opposite. That's when the fade edge exists,
not on public agreement alone.

## LAD @ PHI 7/22 (historical — Andy's original poster case)

Was cited as "consensus 100% LAD should fade to PHI". LAD won 9-5.
Consensus was RIGHT. Consistent with the 65% pattern above.

## What the app should still surface

- 100% consensus + model agrees → confirmatory signal, ride it
- 100% consensus + model DISAGREES → potential fade (bucket calibration
  will decide once n≥20 per (surface, band, contra) combo)
- 100% consensus + model NEUTRAL → monitoring only

## What NOT to say to users

- ❌ "Fade the unanimous chalk" as a blanket call — this was wrong
- ❌ "Yesterday's SF/STL both lost as chalks → pattern" — small sample
  fooled me on 7/26. The 30d data has always said the opposite.

## Related

- [[project_external_transparency_differentiator]] — original vision
- [[project_contrarian_lens_dead_722]] — related 60d finding
- [[feedback_ml_vs_rl_conflation]] — another example of me
  misreading limited data as pattern
- [[feedback_user_doubt_is_signal]] — user's live hypothesis IS data
- [[feedback_confidence_in_first_pass]] — honest confidence bounds
