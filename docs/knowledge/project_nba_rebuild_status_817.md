---
name: project-nba-rebuild-status-817
description: "2026-08-17 NBA rebuild status — infra scaffolded end-to-end (ESPN + Odds API), pending model choice + Basketball Reference scraper before Oct 22 opener"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-18T01:05:04.691Z
---

Follow-up to [[project-nba-reset-817]] — reset completed same day. This
memo tracks REBUILD status.

## Infrastructure SHIPPED (2026-08-17)

**Data client:** `mlb_pipeline/nba_data_client.py` — ESPN API
(site.api.espn.com). Verified 11 games returned + resolved on 2024-11-10.
**stats.nba.com WAS TESTED and BLOCKS local IPs** (Cloudflare, timeouts
>60s). Do not use stats.nba.com unless routing through nba_api package.

**Pipeline scripts:**
- `nba_game_context.py` — schedule + odds + rest/B2B
- `nba_resolve_results.py` — grader from ESPN scoreboard
- `backfill_nba_team_tendencies.py` — L10 rolling window

**Migration:** `20260817_nba_foundation.sql` — 5 tables (nba_game_context
with 55 columns, nba_game_results, nba_injuries, nba_team_stats,
nba_player_game_logs) + RLS tightened.

**Workflow:** `nba_pipeline.yml` — daily 8am + 6pm ET.

**Signals:** 10 team_form_season already seeded via universal migration.

## Pending user decisions

**Model choice** (all viable, ordered by build complexity):
1. Elo rating (simplest, ships fast, ESPN-only)
2. Four-factor efficiency (needs BBRef scraper)
3. XGBoost trained on features (best predictive, longest build)
4. Ensemble of multiples (best long-term)

## Pending data sources

- Basketball Reference scraper (four-factor + advanced stats)
- Injury scraper (ESPN or Rotowire, daily refresh)
- Historical odds for backtest (options: sports betting review scrape,
  or accept teamrankings season-aggregate as the historical layer)

## Prop pipeline — not scaffolded

Deferred until model choice locked. NBA props are massive
(PTS/REB/AST/3PM/etc). Reuse `prop_ensemble_scorer.py` shape when ready.

## Rollout

1. Apply `20260817_nba_foundation.sql`
2. Preseason ~Oct 8 — cron auto-populates data
3. Model choice + prop pipeline in September

Related: [[project-nba-reset-817]], [[project-nhl-scope]]
