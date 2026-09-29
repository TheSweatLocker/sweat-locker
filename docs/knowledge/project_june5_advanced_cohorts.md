---
name: june5-advanced-cohorts
description: "6/5 advanced backtest n=640 — long-rest ace DOG, stacked confluence+rest, Coors x high-GB findings + ship configs"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Shipped 6/5 (commit b79ba0f). Backtest harness: [_backtest_advanced_605.py](mlb_pipeline/_backtest_advanced_605.py).

**Shipped cohorts:**
- Long-rest ace DOG SP: xERA ≤ 3.70 + days_rest ≥ 5, picking the dog RL → 62.0% on n=158 (+0.184 EV at -110). Lives in SIDE dim as +6 driver.
- STACKED net=4 + long-rest dog SP: 85.0% on n=20 (+0.623 EV at -110). +6 additional driver on top of the cohort above when confluence net=4 ALSO points at the same dog. Combined +12 makes it the strongest single play type we have.
- Coors x high-GB OVER: park_run_factor ≥ 115 + avg GB% ≥ 0.50 → 66.7% OVER on n=15 (+0.273 EV at -110). +4 driver in TOTAL. Counter-intuitive — high-GB doesn't save the under at extreme elevation (sinkers flatten, GB contact through synthetic infield).

**Watchlist (n too small to ship, monitor in production):**
- Bullpen tax exactly ≥4 (bp_relievers_3d): fade taxed team ML 72.7% n=11 (+0.388 EV). At ≥5 the edge dilutes to 52% (relievers regress to mean).
- Both-rebound (L7 OPS - L14 OPS ≥ +0.040 on BOTH lineups): OVER 66.7% n=9.
- Slump-vs-rebound side-pick: too small to act on (n=6/9).

**Bug avoided:** column name was `home_sp_days_rest` in mlb_game_results but `home_days_rest` in mlb_game_context. The new code reads both with fallback. Caught via slate verification (no LRA driver fired until field name fixed).

**Why:** User requested 4 deep cohort backtests after the n=4 confluence rework. Result: 3 new shippable edges with bigger samples than the confluence rework (n=158 vs n=23 on the LRA cohort).

**How to apply:** When a game shows BOTH "Long-rest ace as DOG" AND "PEAK confluence" SIDE drivers, that's the 85% lifetime cohort — treat it as auto-PRIME tier candidate. When Coors is on the slate, check if both starters average GB% ≥ 0.50 before doubting the OVER.

Linked: [[june5-cohort-audit]] (parent n=640 scan), [[june5-side-dim-rework]] (the v3+v4 work that preceded this).
