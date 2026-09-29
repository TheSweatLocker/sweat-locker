---
name: nfl-lr-shadow-gap-913
description: "NFL primary_play missing _lr_ml_shadow on Week 1 games — LR predictor didn't run OR strip step removed it. Blocks the LR-agrees/warns gate (93.6% vs 4.3% edge)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T16:12:23.302Z
---

**✅ RESOLVED 2026-09-14 · commit 657a738d**. Root cause: `recompute_nfl_primary_play.py` had forward-only date window (`start_date + days`), so games stamped once at seed-time and never revisited by a later same-day recompute stayed orphaned. Two casualties: NE@SEA (9/10) + SF@LA (9/11), both stamped 2026-07-22 and untouched since. Fix: added `--lookback` param (default 7d) to argparse + `fetch_ctx_window`. Retroactive impact from a single re-run: BAL@IND 9/13 promoted COVERAGE→PRIME (already a graded win — Wk1 PRIME sides now 1-0 not 0-0), DAL@NYG 9/14 promoted LEAN→PRIME (live pick, LR gave DAL 65%), BUF@HOU + GB@MIN 9/13 COVERAGE→STRONG (graded wins).

**Ported 2026-09-15 · commit 935a6c94** to `recompute_ncaaf_primary_play.py`. NCAAF in-season, same "long gap between seed + game" pattern.

**Pattern to inherit on NBA/NHL/NCAAB recompute scripts (when created for Oct/Nov season starts)**: any sport-specific `recompute_*_primary_play.py` MUST include `--lookback` argparse param (default 7d) + `fetch_ctx_window(start_date, days, lookback)` signature that shifts `start` behind the anchor. See `recompute_nfl_primary_play.py:54-67` and `recompute_ncaaf_primary_play.py:48-71` for the reference implementation. Failing to include this = same orphaned-row bug when late-week games hit the LR-shadow backfill code in defensive_gates.py.

**ORIGINAL DISCOVERY (2026-09-13 during 4-loser pattern audit)**:

**Fact:** All 4 losing NFL Sharp Card picks today (MIA ML, GB ML, PHI -6, LAC ML) had `primary_play._lr_ml_shadow = {}` or missing entirely. Meanwhile:
- DEN @ KC MNF game had full LR shadow: `{'p_home_win': 0.6071, suggested_side: 'HOME', suggested_tier: 'STRONG'}` + `_lr_total_shadow` + `_goat_shadow` + `_logreg_shadow`
- Some NFL games (verified 50 games with LR populated, mostly 2027 dates — looks like sample/seed data) have LR
- NCAAF has LR shadow universally populated — 45+37 sample already produced 93.6%/4.3% agree/warn split

**Root cause hypothesis (needs verify):** The NFL LR predictor `nfl_ml_logreg_predict.py` either:
1. Didn't run for these 2026 W1 games (cron miss OR filter)
2. Ran but got skipped by a `if not primary_play: return` early-out
3. Was stripped by a downstream primary_play recompute step

**Why it matters:** The LR-warn hard-cap gate I shipped (commit `8e1cdbfd`) relies on `_lr_ml_shadow.p_home_win`. If the field is missing, the gate no-ops on that game. All 4 losers today had NO LR data → gate couldn't help.

If LR had populated + we'd applied the cap, some of those 4 losses might have been PASSes. Or LR would have agreed and we'd have known the pick was sound.

**How to apply:**
1. First-thing Mon/Tue diagnostic: check when `nfl_ml_logreg_predict.py` last wrote to nfl_game_context primary_play._lr_ml_shadow for 2026 games. Query: `SELECT COUNT(*), MIN(game_date), MAX(game_date) FROM nfl_game_context WHERE season=2026 AND primary_play->>'_lr_ml_shadow' IS NOT NULL`
2. Rerun predictor manually for W1 games + verify writeback path
3. Add watchdog: alert if <90% of active-week games have populated `_lr_ml_shadow` before Sharp Card lock

**Related:** [[project_lr_shadow_stale_909]], [[feedback_lr_daily_verify]], commit `8e1cdbfd` (LR-warn cap depends on this data), commit `23cfdd70` (QB column bug — same class of "NFL data pipeline silent gap").
