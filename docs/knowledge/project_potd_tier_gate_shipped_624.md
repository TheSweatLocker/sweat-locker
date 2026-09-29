---
name: project-potd-tier-gate-shipped-624
description: 6/24 shipped tier discipline gate into POTD selector after 0-6 loss streak; backtest saved 3 losses with 0 new losses created
metadata:
  type: project
---

User raised POTD bleeding 0-6 over last week. Audit confirmed last 20 POTDs went 5W-9L-4P (36% hit rate). Root cause: POTD inherited the composite OVER-bias (composite picks OVER 72% vs actual 49%).

**Shipped (commit d5e5552):**
- Wired `tier_discipline_gate.evaluate_total()` into `play_of_day.py` as post-selection validator
- Total picks must clear PRIME/STRONG/LEAN/ELITE-UNDER tier or get rejected
- Rejection searches replacement from audit_pool/value_pool
- If no replacement passes, falls back to REST DAY (no-play marker)
- ML/RL/prop picks pass through unchanged (separate gate work queued)

**Backtest results on last 20 POTDs:**
- 10 passed gate (unchanged)
- 7 forced REST DAY
- 3 losses saved (REST DAY instead of LOSS): 6/23 MIL@CIN, 6/14 PHI@MIL, 6/4 OAK@CHC
- 0 new losses created

**Gate still passed through 3 losses (6/22, 6/19, 6/18):** PRIME-tier setups within their 71% historical hit band — variance, not selector failure.

**Why this matters:** Net effect is lower volume + cleaner picks. The gate trims the worst picks (SKIP tier — mild magnitude, 2-of-3 only, middle-UNDER trap zone) without eliminating variance entirely.

**How to apply:** When user questions POTD output, check if pick has `_gate_tier` metadata — that confirms it cleared the discipline gate. REST DAY publishes a clear noPlay flag in the cache for app rendering.

**Queued tomorrow:**
1. Similar gate for ML/RL POTD picks (resolver STRONG/ELITE filter)
2. PRIME pitcher prop fallback if no total candidate clears gate (64-86% hist hit rate)
3. ML walk-forward backtest to identify ML gate thresholds

Related: [[project_total_v7_shadow_shipped_623]], [[project_total_model_retrain_625]]
