---
name: june5-cohort-audit
description: 6/5 outside-the-box backtest on n=640 graded games — actionable cohorts to ship vs queue vs avoid
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

6/5 cohort scan (_backtest_outside_box.py) on n=640 graded games surfaced these:

**SHIPPABLE EDGES (n≥30, +EV, distinct from existing scoring):**
- Confluence net=±4 + FAV ML: 9-4 (69.2%, +0.322 EV at -110) — NEW. Mirror image of the net=4 + DOG RL 82.6% play, but on the favorite side.
- Confluence net=±4 → UNDER (any direction): 18-13 (58.1%, +0.109 EV) — confluence-net-as-volatility-proxy lights up UNDER.
- v4 wins v3-v4 SIDE disagreement: 20-15 (57.1%, n=35) — when models split, trust v4. Already partially encoded by v4 spread bands in [[june5-side-dim-rework]].
- xERA gap 1.5-2.0 → OVER: 50-38 (56.8%) — counter-intuitive (big pitching gap usually = under).

**EXPLICIT FADES (don't surface these on cards):**
- Fav ML -130 to -150: 80-80 (50% coinflip, -0.143 EV at -140) — money-burner band.
- Dog ML +175 area: 10-26 (27.8%, -0.236 EV) — trap zone, no edge.
- ALL "FAV RL" buckets at confluence net 0-3: 31-34% — confirms structural fav-RL fade.

**LIFETIME ML / RL TRUTHS (record once, stop relitigating):**
- Fav ML lifetime: 53-58% depending on price band, but only -110/-130 band is +EV.
- Dog ML lifetime: 50% at +120, 27.8% at +175 (NOT a smooth curve — middle dogs are traps).
- Dog RL lifetime: v3 60.7%, v4 65.9% (v4 better predictor on dog RL specifically).
- Confluence net=4 + DOG RL: 82.6% (n=23) is THE strongest single-signal cohort in the entire system.

**Why:** User explicitly requested "we should be weighing different outcomes for ML, RL, totals... find more confident method." This scan was the answer; the per-band breakdowns reveal that ML edges are narrow and price-band-specific, not blanket.

**How to apply:** When scoring SIDE/TOTAL dims, treat confluence net=4 as the headline structural signal. When picking off a fav ML -130 to -150 game, require a non-confluence reason. v3-v4 disagreement defaults to v4. Reference these cohorts before adding new model features so we know the baseline ceiling.

Linked: [[june5-side-dim-rework]] (ship config), [[may17-confluence-audit]] (prior confluence work).
