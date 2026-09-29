---
name: project_nba_offseason_rebuild
description: NBA offseason (Jul-Sep 2026) workstream — model + prop pipeline parity with MLB. Strategic positioning play, not v1.0 launch blocker.
metadata:
  type: project
---

NBA offseason 2026 (roughly July - mid-September, before Oct 1 calendar re-open) is the window to do serious NBA work. BDL sub cancelled mid-June 2026; the season_calendar + key-presence gate handles auto-skip. Re-subscribe in October.

Two workstreams:

**1. NBA model rebuild**
Current NBA infra: nba_pipeline.py (BDL team stats), nba_picks_generator.py (spread/total/ML conviction tiers), nba_pick_logger.py (game logging + BDL resolver), generate_nba_game_reads.py (Jerry reads). Spread/total/ML tier thresholds are "informed-guess pre-audit" (SPREAD_LEAN 2.0, SPREAD_STRONG 4.0, SPREAD_PRIME 6.0). Audit cohort calibration data accumulated during last season — review at start of offseason.

Specific rework items:
- Tier threshold recalibration once we have full season-end audit data (n≥30 per tier minimum)
- Cohort engine for NBA (transfer universal arch from MLB — see [[project_cohort_engine_universal_architecture]])
- Add NBA equivalents of MLB's mastery layer (player-vs-team, recency drift)
- Per-team pace/usage/rest cohorts (b2b, 3-in-4, long-rest favorite)
- XGBoost runs-equiv model for NBA totals? Possibly.

**2. NBA prop pipeline parity with MLB**
Current state: NBA props only get graded via resolve_props.py BDL boxscore lookup (sport='NBA' branch). No NBA-specific conviction scoring, no tier x type tracking, no cohort gating, no sweat card surfacing. The MLB prop pipeline is the moat — NBA should match it.

What to build (mirror MLB structure):
- `nba_props_pipeline.py` — equivalent of `props_pipeline.py`
- Per-prop-type scorers: points_over/under, rebounds, assists, threes, pra (points+reb+ast), steals, blocks
- PRIME/STRONG/LEAN tier framework + conviction floats
- Cohort engine for prop-specific signals: pace, opposing def rating vs position, usage rate, recency drift, rest, b2b
- Player-vs-team mastery layer (similar to pitcher_vs_team_era for MLB)
- Hook into live tier x type framework (track_live_tier_record.py already supports prop_type_breakdown — just feed it NBA data)
- Sweat card surfacing for NBA props
- Jerry attribution validator extension (verify player team via NBA game context — preempts the same trap that hit Cease in MLB)
- Source-gate pattern (see [[feedback_source_gate_pattern]]) — gate sample size at BDL fetch, not at scorer

**Why this matters for app success:**
4-sport conviction product (MLB + NFL + NBA + NCAAB) with depth in each = clear differentiation vs single-sport apps and vs broad-but-shallow tout apps. User's read: "if we have a strong product for NBA, NFL, NCAAB, and MLB... it increases app success likelihood." Treat this as strategic priority not nice-to-have.

How to apply:
- Don't try to build during NBA in-season — too much daily-card pressure
- Use offseason (Jul-Sep) when daily MLB workload is reduced
- Phase 1 (Jul): season-end audit + tier recal + cohort port
- Phase 2 (Aug): NBA prop pipeline build
- Phase 3 (Sep): Jerry NBA prop reads + sweat card integration
- Target: Oct 1 preseason = re-enable BDL + ship NBA v2.0 in same window

Related: [[project_cohort_engine_universal_architecture]] [[project_post_launch_roadmap_may_to_nfl]] [[feedback_source_gate_pattern]] [[project_ncaab_scope]]
