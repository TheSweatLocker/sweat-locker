---
name: rolling-rollup-architecture-901
description: 9/1 architecture — per-section rollup tables (single source of truth per team × section) update on cron; game card just reads. Cross-sport uniformity.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-01T18:59:01.577Z
---

**Architecture decision (2026-09-01):** Every game-card section reads from a **denormalized rolling rollup table** that updates on cron. Layout is decided/wired in code once; each section pulls from a purpose-built dataset. New sport onboarding = populate the tables, layout works automatically. Zero per-sport rendering branches for data pathways — only per-sport data.

**Target tables (Tier 1 pre-launch):**
1. `team_recent_games` — team's last N with score + ATS + O/U grades. Matview off `{sport}_game_results`. Cross-sport (sport column).
2. `team_situational_records` — W-L-P per (team × market × filter). Extends existing `{sport}_team_home_road_tendencies` matviews + fills MLB/NCAAB/NHL gaps + adds L10/divisional/post-loss filters.
3. `team_stats_rolling` — raw per-game averages + rank per (sport, team, stat_key). Kills the `home_team_stats_summary` JSONB blob anti-pattern on NFL/NCAAF ctx and the client-side compute + fuzzy substring team-matching in [`NCAAFTeamMatchupCard`](app/components/GameDetailV2.tsx#L1284-L1337).

**Tier 2 (post-launch or parallel):**
4. `team_stat_leaders` + headshot ingestion — biggest scope, least critical. App can render placeholder initials until this lands.

**Test sports for Tier 1 rollout:** MLB + NCAAF first. Verify data accuracy + refresh cadence for a couple days before porting to NFL/NBA/NCAAB/NHL.

**Why:**
- User quote 2026-09-01: "The goal here is to have uniformity across sport game cards, wire it up, test on MLB/NCAAF next couple days, ensure data is accurate and rolling"
- Related audit findings: [[project_game_cards_two_layer_901]] · [[feedback_data_visibility_over_layout]] · [[feedback_ui_and_pipeline_in_parallel]]

**How to apply:**
- Any new section on any game card: define the rollup table BEFORE writing the component
- Rendering code reads directly from Supabase; never recompute stats client-side
- Refresh cadence: matview refresh RPC called from the pipeline's resolver step (post game-grading) so records lag <30 min behind the last game finish
- Cross-sport rendering means one query pattern per section — filter by `sport IN (X, Y)`, not sport-branching JSX
- Sport-launching = populate the tables. Zero UI changes.

**Build order (in flight):** team_recent_games → team_situational_records extension → team_stats_rolling refactor → team_stat_leaders + headshots (post-launch).
