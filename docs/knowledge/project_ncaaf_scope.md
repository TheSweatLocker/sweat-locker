---
name: project_ncaaf_scope
description: NCAAF model planned scope — Spread/ML/Total only, NO player props. Mirrors NCAAB scope decision. Season window Aug 25 - Jan 15 in season_calendar.
metadata:
  type: project
---

NCAAF v1.0 ships **spread, ML, and total** model coverage only. No player props.

Reasoning matches [[project_ncaab_scope]] verbatim:
- Sportsbook coverage for CFB player props is thin and inconsistent
- Data quality for player-level CFB stats is much worse than NFL (no nfl_data_py equivalent that's as clean)
- The signal-to-noise ratio doesn't justify the build cost when team-level (spread/ML/total) is where the actionable edge lives

Build plan (target NCAAF v1.0 launch for 2026 season):
- Data source: cfbd-api package (CollegeFootballData.com) — free tier sufficient for v1.0
- Phase A (when?): backfill 2024-2025 seasons + market lines via Odds API
- Phase B: cohort engine (transfer universal arch from MLB/NFL — see [[project_cohort_engine_universal_architecture]])
- Phase C: Jerry NCAAF game reads + sweat card surfacing

Season window already in season_calendar.py: Aug 25 - Jan 15.

Likely build sequence: NFL Phase 2 (August 2026) first, then NCAAF Phase A piggy-backs since it's the same data structure (drives, EPA, situational splits).

When to revisit player props for CFB: never for v1.0/v1.1. Possible v2.0 consideration if sportsbook coverage materially improves AND cfbd player data quality improves.

Related: [[project_ncaab_scope]] [[project_nba_offseason_rebuild]] [[project_cohort_engine_universal_architecture]]
