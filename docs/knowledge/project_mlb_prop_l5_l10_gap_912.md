---
name: project_mlb_prop_l5_l10_gap_912
description: 🚨 Most MLB prop cards in Prop Jerry are missing L5/L10 bar graphs on stat cards. NOT the same as the hardcoded index prop L5/L10 pattern — this is a separate ingestion/render gap. Andy called out 2026-09-12 morning; do NOT touch index prop code.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-12T12:03:17.227Z
---

# MLB Prop L5/L10 bar graph coverage gap

Andy 2026-09-12: "most mlb props missing l5/l10 bar graphs n in prop
jerry stat cards (not hardocded index propnelm so dont even fucking go
threre)"

Two separate systems that must not be conflated:

1. **Index-prop L5/L10** — hardcoded pattern for index-relevant props
   (K props etc). Existing, working, DO NOT MODIFY.
2. **Standard MLB prop stat card L5/L10** — the bar graph that renders
   on the Prop Jerry stat card for pitcher outs/Ks/BB/ER/HA props,
   batter hits/TB/RBI/runs, etc. **This is the gap.**

Most standard prop cards ship without the L5/L10 graph. Reason unknown
yet — needs investigation:
- Is `player_game_log` populated for the players in question?
- Is the query filter too narrow (e.g. season year mismatch)?
- Is the stat_type mapping missing entries for newer prop types?
- Related: [[project_prop_jerry_coverage_gap_911]] mentions only 32/666
  MLB props get jerry rows — could be same-root, could be different.

## When investigating

- **Never** modify or reference index-prop L5/L10 code as the fix
  path. Andy called this out explicitly.
- Check `mlb_pipeline/backfill_prop_l5_l10_lookback.py` (or NFL
  equivalent) to see the ingest logic.
- Check the app render path for standard prop stat cards — likely
  reads from `player_game_log` or similar.
- Compare a prop card WITH L5/L10 vs one WITHOUT and identify the
  data-path divergence.

## Priority

Andy flagged it "high priority" on launch weekend. Queue right after
the grading pipeline fixes ([[feedback_grading_zero_fail_912]]) land
and stabilize.
