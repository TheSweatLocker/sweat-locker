---
name: Spread delta sign-convention bug
description: spread_delta math inflates every game by 2x the posted spread due to mixed run-diff / sportsbook conventions — must fix + retune thresholds pre-launch
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**THE BUG:** `projected_spread` uses run-differential convention (positive = home wins by X). `close_spread` uses sportsbook convention (negative = home favored). Pipeline computes `spread_delta = projected_spread - close_spread` without normalizing conventions, which DOUBLE-COUNTS the sign flip. Example 2026-04-24: Braves projected +1.76, posted -1.5 → true agreement is 0.26 runs, but stored as +3.26.

**THE FIX:** Change `spread_delta = projected_spread - close_spread` to `spread_delta = projected_spread + close_spread` (i.e. subtract the run-diff conversion of close_spread, which is `-close_spread`). ~1 line change in game_context.py.

**DOWNSTREAM IMPACT (inflated 2x close_spread on every game):**
- Play of the Day ML HIGH CONVICTION tier (threshold 3.0+)
- Daily Degen ML leg selection (threshold 3.0+)
- Generate_props any signal referencing spread_delta
- Jerry narrative when citing the magnitude

**WHAT IT DOES NOT AFFECT:**
- Direction of model lean (who is favored) — that's always correct
- NRFI scoring (completely separate calc)
- Projected totals / total_delta (different math)
- wRC+, xERA, bullpen, platoon, K gap signals (independent)
- Pipeline props conviction (uses K%/xERA/wRC+ directly, not spread_delta)

**WHY THIS WAS MISSED:** All prior audits were results-based (hit rate by tier, MAE against actuals) not math-based. No one walked through a calculation by hand to verify convention alignment. The spread model's mediocre hit rate — which led to disabling it — was LIKELY caused by this bug amplifying noise games into "HIGH conviction" labels that regressed.

**Why:** User caught this looking at today's Braves read (+1.76 model vs -1.5 market = intuitively ~0.3 runs, but pipeline said +3.3). Called out on social media risk. Trust-breaking bug if a sharp bettor audits the math.

**How to apply:** 
1. Before any other model retraining work, fix this math first (~1 hour including threshold retune).
2. Retune HIGH/STRONG/LEAN conviction thresholds for ML plays to match the now-accurate magnitude.
3. Before launch, systematically audit EVERY derived metric by working one game through by hand (total_delta, spread_delta, k_gap, dog_edge, NRFI components). Results-audits are not enough.
4. Cross-check: look at the spread model's historical hit rate on games where the NEW (corrected) delta was ≥1.5 vs ≥3.0. The corrected threshold probably unlocks spread picks that are currently sitting in the "too weak to play" bucket.
