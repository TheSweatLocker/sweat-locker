---
name: nfl-phase-2-shipped-809
description: NFL Phase 2 fully shipped 8/9. Two-model system (Matchup-EPA + Fantasy Panel) + sharp-fade rules + props Panel-informed. 66.7% backtest side accuracy.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T19:00:20.720Z
---

**2026-08-09**: NFL Phase 2 shipped end-to-end for Sept 4 launch.

**Two projection models** (analog of MLB Jerry + V4 + Panel):
1. **Matchup-EPA** (`projected_total`, `model_pred_home_points`) — offense × opp defense × HFA. Uses `nfl_team_defense_stats` (backfilled from historical results).
2. **Panel** (`panel_pred_total`, `panel_pred_home_pts`) — sum of individual player fantasy projections, ensemble Sleeper + ESPN, K=0.245 calibration constant. **66.7% side accuracy on 2025 wk1 backtest.**

**primary_play resolver enhanced**:
- Spread lane: heavy_home_dog PRIME + STRONG w/ edge≥3.5 + Panel confirm
- Total lane: STRONG w/ edge≥4 + Panel confirm, plus Panel-only LEAN at edge≥4 conf≥0.6
- ML lane (NEW 8/9): Model+Panel agree on same side, spread>=3, price -140 to +150 → LEAN/STRONG. Skips heavy fav >-200 per juice-trap rule.

**Sharp-fade rules for NFL** (`nfl_sharp_fade_rules.py`):
- 6 rules total (MODELS_OPPOSE_SHARP_TOTAL/ML/SPREAD, SHARP_ON_ROAD_TEAM, SHARP_ON_AWAY_FAV, SHARP_LIGHT_JUICE, SHARP_OPPOSES_CONFLUENCE, SHARP_ON_HEAVY_HOME_FAV)
- All LOG mode until NFL sample accumulates via `sharp_fade_audit_trail`
- Same cap policy as MLB (1 ACTIVE → LEAN, 2+ → READ)

**Sharp-fade audit trail wired to NFL** via `--sport NFL` flag in `sharp_fade_audit.py`. Auto-writes per-game rows via nightly cron. Auto-tunes rule modes as data accumulates.

**Prop pipeline Panel-informed** (`nfl_generate_props.py`):
- Blend: **0.50 fantasy_proj (Sleeper+ESPN) + 0.30 L4 + 0.15 season + 0.05 baseline** × opp_adj
- Covers: player_pass_yds, player_rush_yds, player_reception_yds, player_receptions, player_anytime_td
- Injury-aware (Sleeper/ESPN update per week)

**Refit engine deferred**: needs graded outcomes to train weights (2026 season hasn't started, `nfl_pipeline_props.result` is empty). Rebuild post-Week-4 with real data.

**Cron wired** in `.github/workflows/nfl_pipeline.yml`:
1. Sleeper + ESPN projection pulls (before game_context)
2. game_context build w/ Panel populate
3. nfl_generate_props uses fresh projections
4. sharp_fade_audit --write-today --sport NFL
5. sharp_fade_audit --backfill-results (nightly resolver mode)

**File map**:
- `mlb_pipeline/nfl_team_defense_backfill.py` — populates `nfl_team_defense_stats`
- `mlb_pipeline/nfl_sleeper_projections_pull.py` — Sleeper API pull
- `mlb_pipeline/nfl_espn_projections_pull.py` — ESPN Fantasy pull
- `mlb_pipeline/nfl_panel_projection.py` — team aggregator w/ ensemble merge, position caps
- `mlb_pipeline/nfl_game_context.py` — projection compute + primary_play resolver (Panel-aware)
- `mlb_pipeline/nfl_sharp_fade_rules.py` — 6 sharp-fade rules
- `mlb_pipeline/nfl_generate_props.py` — props w/ fantasy blend
- `supabase/migrations/20260809_nfl_team_defense_stats.sql`
- `supabase/migrations/20260809_nfl_player_projections.sql`
- `supabase/migrations/20260809_nfl_panel_pred_columns.sql`

**Known limitations at ship**:
- Sleeper doesn't project preseason (blank for Aug preseason games — Panel only lights up regular season)
- ESPN uses per-game avg (statSplitTypeId=2), not week-specific — stable baseline
- Refit engine deferred to post-Week-4
- Sharp-fade rules all LOG mode initially (auto-activate as NFL sample accumulates)

Related: [[project_nfl_phase1_ready_721]], [[project_nfl_launch_readiness_806]], [[project_sharp_money_fade_808]].
