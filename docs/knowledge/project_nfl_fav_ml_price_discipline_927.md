---
name: project_nfl_fav_ml_price_discipline_927
description: NFL fav-ML picks win 65% but lose money (-4.9% ROI); PRIME is worst at -22.8%; engine has never picked a dog ML and has no price cap
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-27T22:51:03.579Z
---

Measured 2026-09-27, NFL weeks 1-3 plus the 9/27 early slate. n=23 fav-ML
picks with closing prices and results. Small sample -- suggestive, not
settled -- but it lines up with [[feedback_heavy_fav_ml_trap_803]].

**Andy's question was: when the engine picks ML on a favourite, how often
does the dog cover?** Answer: 11 of 23 (47.8%), against a 52.8% dog-cover
baseline on all matched games. So the dog covers LESS often in our fav-ML
games than baseline -- the favourite SELECTION is marginally fine.

**The losses are blowups, not near-misses.** Cross-tab of the 23:
  11  fav won ML and covered            clean
   3  fav won ML but dog covered        we cashed, our number was wrong
   8  fav lost outright (34.8%)         dog won AND covered

**The problem is price, not selection.**
  record 15-8 (65.2%)   avg price -344   BREAKEVEN 68.6%   ROI -4.9%
  (-1.13u on 23u flat)

**By tier -- conviction is INVERSELY related to profit here:**
  PRIME     6-5  (54.5%)  breakeven 67.7%  ROI -22.8%  n=11
  STRONG    5-2  (71.4%)  breakeven 62.5%  ROI +15.6%  n=7
  LEAN      2-0 (100.0%)  breakeven 66.5%  ROI +53.8%  n=2
  COVERAGE  2-1  (66.7%)  breakeven 87.6%  ROI -26.6%  n=3
PRIME fav-ML is the most confident thing the engine does and it is the
worst performer.

**No price discipline exists on this path.** Picks included DET ML at
-2800 (96.6% implied -- a win nets 0.036u, a loss costs 1.00u), KC -600,
SEA -410. POTD has a -250 juice gate ([[feedback_potd_juice_gate_803]]);
the sides/ML engine path has nothing equivalent.

**The engine has NEVER picked a dog ML.** 0 of all matched NFL ML picks.
This sits oddly beside [[project_sp_plus_compression_927]], where NFL
MARGIN projections lean dog 76-96% of the time. Two paths, opposite
biases, neither calibrated -- and the ML path performs better precisely
because it ignores the compressed margin projection.

**Cheapest fix available: a price cap on fav ML**, not model work. The win
rate is not the problem.
