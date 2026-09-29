---
name: project_bdl_paused_616
description: BDL (Ball Don't Lie) subscription paused 2026-06-16 — NBA season ended. Offseason migration plan: port to free nba_api package before resubscribing. Re-evaluate at Oct preseason.
metadata:
  type: project
---

User cancelled BDL subscription on 2026-06-16 (NBA season ended). Plan to migrate from BDL → `nba_api` Python package (free, official NBA Stats API wrapper) during offseason NBA workstream rather than auto-resub in October.

**Why migrate:**
- `nba_api` gives substantially richer data than BDL (lineup combos, shot zones, defense matchup, hustle, tracking, on/off splits, possessions) at $0
- Same data source NBA front offices actually use
- Same money saved across the year (~$540)

**Why not panic-migrate now:**
- NBA out of season — no daily card pressure
- Code change is real (rewrite team_stats fetcher + player stats fetcher + boxscore resolver)
- Use offseason calmly, no rush

**When to revisit:**
- If `nba_api` proves unreliable in build (frequent endpoint breaks, severe rate limit pain) → re-up BDL for season
- If everything works smooth → keep BDL paused forever

**Pipeline safety:**
- season_calendar + BDL_API_KEY two-layer gate (commit 28ebfdc) auto-skips all NBA pipeline steps while sub is paused
- No manual workflow YAML changes needed

**Migration plan (offseason):**
- Replace `nba_pipeline.py` BDL endpoints with `nba_api.stats.endpoints.*` equivalents
- Replace `nba_pick_logger.py` --resolve boxscore lookup
- Replace `resolve_props.py` NBA branch BDL player stats lookup
- Add ESPN injury scrape (separate concern from BDL/nba_api)
- Test against historical comparison: same game, BDL output vs nba_api output

Related: [[reference_data_source_strategy]] [[project_nba_offseason_rebuild]]
