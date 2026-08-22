# Workflow Reorder Recommendation — mlb_pipeline.yml

**Status:** LOW PRIORITY. The pick_snapshots grader fallback (`707eb087` + `2d0df2b5`) recovers 372/596 previously-stuck snapshots. Only 33 remain ungraded — those need either this reorder or a game-results direct grader.

## Current order (mlb_pipeline.yml)

```
1. pitcher_stats
2. bullpen
3. team_offense
4. savant
5. game_context
...
21. generate_pipeline_props     ← WIPES today's props BEFORE upserting fresh
...
~34. grade_props                ← runs AFTER wipe
     grade_pick_snapshots       ← runs AFTER wipe
```

## Problem

When `generate_props.py` wipes today's props (line 3300, `wipe_todays_props()`)
during regeneration, any prop_ids that snapshots reference get REPLACED with
NEW auto-increment ids. Snapshots taken at card lock (e.g., 10 AM) reference
the ORIGINAL prop_id, which no longer exists by 6 PM after multiple wipes.

Impact: snapshot grader can't match by prop_id → skips → snapshot stays
ungraded → Sharp Card historical track record incomplete.

## Fix (already partially mitigated)

The `grade_pick_snapshots.py` fallback layers shipped 8/21 handle this:
1. **Fallback 1:** match by (player_name, prop_type, prop_line, direction)
   against `mlb_pipeline_props` (recovers 191/596)
2. **Fallback 2:** same key against `prop_playbook_decisions` which is
   persistent (recovers 372/596 total → 33 remain)

## Full-solve option (LOW PRIORITY)

Reorder mlb_pipeline.yml to run `grade_pick_snapshots.py` BEFORE
`generate_props.py` in each cron cycle. Every cycle would grade
snapshots against the CURRENT prop table before it gets wiped/regenerated.

### YAML change (5 lines)

Move this step:
```yaml
- name: Resolve prop grades
  ...
  python grade_pick_snapshots.py --days 7
```

From position ~34 (after `generate_pipeline_props`) to position 20
(BEFORE `generate_pipeline_props`).

### Risk

- Snapshots grade against the previous cycle's mlb_pipeline_props state,
  which may not reflect the latest line movements
- Marginal — snapshots are DATED and their prop_ids are anchored to a
  specific point in time, so grading against slightly-older state is
  fine as long as game results are available
- Would add ~30 seconds to each cron cycle

## Alternative: game-results-direct grader

Build a per-prop-type resolver that queries MLB StatsAPI + player game
logs directly. Fully independent of `mlb_pipeline_props` state.

Complexity: high (several hundred lines, one resolver per prop type).
ROI at this point: low (only 33 snapshots stuck).

## Recommendation

Do NOT reorder unless the 33-remaining number grows meaningfully or
Sharp Card record accuracy becomes a user complaint. Current 2-layer
fallback handles the important 94% of cases.
