---
name: project-outs-grading-bug-fix-623
description: 6/23 outs prop grading bug — grader returned 0 when IP=0.0 mid-game; 114 records affected, 50 flipped on re-grade
metadata:
  type: project
---

**Bug:** `resolve_props.get_pitcher_outs()` returned 0 when MLB boxscore showed `inningsPitched='0.0'` mid-game. That happened when the grader ran before a game finalized — pitchers were already in the `pitchers` list but hadn't pitched.

The grader then locked the prop as Win with `final_value=0` since `0 < line` for outs_under. Result was never re-graded.

**Fixed 2026-06-23 (commit 8bf3a2c):**
- Added `is_mlb_game_final()` precondition check in `resolve_prop()` for all pitcher prop types
- Defense-in-depth: `get_pitcher_outs()` returns None on IP=0 even after the final check
- Re-graded 114 affected records via `_regrade_buggy_outs.py` → **50 of 114 flipped**

**Impact on historical analyses:**
- outs_under tier hit rates in backtests were INFLATED — bug always favored UNDER (0 < line)
- Earlier we had outs_under PRIME 75-86% — those were partly bug artifacts
- Real outs_under hit rate post-fix likely 50-60%
- Any tier ladder / cohort engine output that included outs_under bin needs re-validation

**How to apply:** When user asks about outs prop performance, flag that historical hit rates may still reflect pre-fix data. Avila / similar outs_under PRIME picks should NOT be promoted at the old conviction level until post-fix sample accumulates (~30 days).

Related: [[project_pending_app_changes]], [[feedback_verify_pitcher_attribution]]
