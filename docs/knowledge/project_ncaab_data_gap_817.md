---
name: project-ncaab-data-gap-817
description: "2026-08-17 NCAAB tendencies port BLOCKED — spread/total/spread_result all NULL on 5,911 games; need odds backfill before port"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-17T22:31:20.028Z
---

Attempted to port team_tendencies infrastructure to NCAAB on 8/17
(same as MLB/NCAAF/NFL). Blocked because `ncaab_game_results` has:

- **0/5,911 games** with `close_spread` populated
- **0/5,911 games** with `close_total` populated
- **0/5,911 games** with `spread_result` populated
- **5,911/5,911 games** with `home_win` populated ✓

**What's needed before port:**
1. Backfill NCAAB close_spread + close_total from historical odds source
   (currently `ncaab_odds_pull.py` writes forward but hasn't backfilled)
2. Compute spread_result / total_result from spread + score (retro-grader)
3. THEN port tendencies (same pattern as
   [[project-team-tendencies-cross-sport-817]])

**Timeline:** NCAAB season starts 2026-11-03. 10+ weeks lead time. Backfill
+ port should complete by mid-October.

**Interim option** (not recommended): ship ML-only NCAAB tendencies (3 of
10 signals). Would provide `home_ml_last10` / `away_ml_last10` /
`home_fades_own_ml_hot` — nothing ATS or O/U. Better to wait for full
data than half-ship.

Related: [[project-team-tendencies-cross-sport-817]] (pattern),
[[project-ncaab-scope]], [[project-ncaab-v4-deferred-814]]
