---
name: project-nfl-launch-readiness-806
description: "NFL prop calibration + Jerry-driven card wire status. Preseason 8/7, Week 1 opener 9/10 NE@SEA. Framework ready, structured Jerry output for games pending."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-06T14:55:45.948Z
---

Snapshot of NFL launch readiness as of 2026-08-06 evening.

**State of NFL infrastructure:**
- `nfl_game_context`: 400 rows (backfilled 2024/25)
- `nfl_game_results`: 400 rows (graded)
- `nfl_pipeline_props`: 4 rows (test data — anytime_td_over/under only)
- `prop_bucket_roi(NFL)`: 0 entries (auto-seeds when NFL prop grades flow in)
- `jerry_reads(NFL)`: 0 rows (game Jerry synthesis writes prose to jerry_cache, no structured output yet)
- `prop_jerry_reads(NFL)`: 1 row (barely started)

**What's WIRED and works:**
- `compute_prop_bucket_roi.py --sport NFL` — sport-parametric ✓
- `prop_tier_calibration.py` — sport-universal `_refresh_from_live_data()` reads any sport's bucket_roi ✓
- `generate_prop_jerry_synthesis.py` — PROPS_TABLE has NFL registered ✓
- `nfl_weekly_card.py` (commit 30ce91a9) — pulls jerry_reads + prop_jerry_reads for NFL alongside cohort picks. Empty today (activates when data flows).

**What's PENDING to unlock full Jerry-driven NFL card:**
1. **NFL Jerry game prompt template upgrade** — current `game_read_rules` NFL doesn't ask for structured CALL block (MARKET/SIDE/CONVICTION). Needs seed script to add SHORT/LONG/CALL sections like MLB.
2. **generate_nfl_game_reads.py refactor** — parse the CALL block from LLM output + dual-write to `jerry_reads` (not just `jerry_cache`). Reuse MLB `parse_synthesis` from `generate_jerry_synthesis.py`.
3. **First NFL prop grades** — flow through `compute_prop_bucket_roi` to seed calibration data.

**Timing:**
- Preseason 8/7: CAR @ ARI (may not be in current NFL context filter)
- Week 1 opener: 9/10 NE @ SEA
- ~5 weeks to Week 1 to complete pending items

**Follow the [[project-calibration-architecture-805]] and [[project-nfl-launch-plan-805]] plans** — cross-season aggregation + lower `MIN_N_ACTIONABLE=10` for NFL still applicable.

**Do NOT** ship MLB fade rules against NFL prop_types assuming they transfer. Each sport discovers its own trap patterns from its own bucket_roi data.
