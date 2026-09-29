---
name: june5-side-dim-rework
description: "SIDE dim rebuilt 6/5 — v4 spread bands added, v3+v4 DOG consensus +12, Jerry spread benched"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Shipped 6/5 (commit 3c137e6): SIDE dim in [play_of_day.py](mlb_pipeline/play_of_day.py) rebuilt after n=640 backtest.

Changes:
- v4 spread bands added (13/8/5/2 at thresholds 2.0/1.5/1.0/0.5). v4 was absent from SIDE scoring before — backtest showed v4 spread predicts DOG RL at 65.9% lifetime, beating v3's 60.7%.
- v3+v4 DOG consensus +12 bonus added (both models agree direction AND that direction is the dog). Lifetime 67.2% / n=125.
- v3 trap-zone (1.5-2.0) rescue now triggers on v4 confirm, not Jerry confirm.
- Jerry spread bands zeroed. Jerry spread direction lifetime = 47.8% / n=67 (coinflip). Kept in DB for transparency; not counted in score.

Verification: 6/4 slate replay surfaced STRONG SIDE on SF@MIL (W), A's@CHC (L), TOR@ATL (W) = 2-1.

**Why:** User explicitly questioned whether we had backtested SIDE/RL at all (only had done totals before this). Result: SIDE dim was overweighting Jerry and ignoring v4 entirely; rework realigns weights to lifetime cohort hits.

**How to apply:** When debugging SIDE-dim scores or explaining drivers in Jerry reads, the headline drivers are now v3+v4 consensus and Confluence net=4. Jerry spread should not appear as a driver. If you see Jerry spread driving a score, that's a regression.

Linked: [[june5-cohort-audit]] (broader backtest findings), [[sweat-dimensional-redesign]] (5/29 dim split that this is part of).
