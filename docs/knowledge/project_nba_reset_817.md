---
name: project-nba-reset-817
description: "2026-08-17 full NBA reset — BDL killed, legacy scripts deleted, rebuild scheduled. Only nba_data_client.py (stats.nba.com starter) survives."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-18T00:35:54.828Z
---

**SUPERSEDES [[project-nba-offseason-rebuild]] and [[project-nba-chalk-trap-fix]]** —
those covered the OLD (BDL-based) NBA stack that no longer exists.

## What triggered the reset (2026-08-17)

1. **BDL API killed as data source** (user decision) — the entire NBA
   pipeline depended on Ball Don't Lie for schedules + boxscores.
2. **Model needs major rebuild anyway** — user's own words: "the whole
   existing NBA model might change a lot"
3. **Better to burn it down cleanly than patch on top** — user directive.

## What was deleted (4 files)

- `mlb_pipeline/nba_pipeline.py` (BDL team stats update)
- `mlb_pipeline/nba_pick_logger.py` (per-game features + market lines)
- `mlb_pipeline/nba_picks_generator.py` (conviction-tier picks)
- `mlb_pipeline/generate_nba_game_reads.py` (Jerry NBA reads)

Plus 4 workflow steps removed from `mlb_pipeline.yml` (they called those
scripts). Replaced with a comment marker for future NBA rebuild.

## What survives (model-agnostic infra)

1. **`mlb_pipeline/nba_data_client.py`** (2026-08-17, new) — fresh
   starter using stats.nba.com API. Has `get_schedule`, `get_scoreboard`,
   `get_team_stats`. Untested against live NBA games (offseason);
   smoke test on 2024-11-05 returned empty (endpoint may need adjustment
   for regular-season dates or additional headers).
2. **10 NBA `team_form_season` signals** — from
   `20260817_season_trend_signals_universal.sql` migration. Fire against
   `nba_game_context.home_season_cover_pct` etc. once ctx table exists
   + populated. Read from `team_season_trends` via `enrich_team_trends.py`.
3. **Universal `team_season_trends` table** — teamrankings scraper
   already supports NBA (30 teams verified pulling clean).

## What still needs to be built (rebuild plan)

Before Oct 22 season opener, ~9 weeks lead time:

1. **stats.nba.com integration** — verify data client works against
   preseason games (starts early Oct). Fix any endpoint issues.
2. **Migrations:** `nba_game_context` + `nba_game_results` tables
   (mirror NHL foundation pattern).
3. **`nba_game_context.py`** — daily cron builder (schedule + odds +
   team stats + season trends enrichment).
4. **`nba_resolve_results.py`** — grader using stats.nba.com scoreboard.
5. **`backfill_nba_team_tendencies.py`** — L10 window (NBA is 82-game
   season like NHL).
6. **Model choice** — user decision pending. Options include: XGBoost
   trained on Kaggle NBA data, NBA-Stats-derived Elo, KenPom-style
   efficiency ratings, or ensemble of multiples.
7. **NBA prop pipeline** — decide scope (props are massive in NBA:
   PTS/REB/AST/3PM/PA/etc.).
8. **NBA workflow YML** — daily cron, mirrors NHL pattern.

## Data source decision (2026-08-17)

User chose: **stats.nba.com (NBA Stats API)** — free, official, similar
shape to NHL API. Requires User-Agent + Referer headers to avoid 403.
Rate-limited — space requests.

## Timing

- Now → Oct 8 (preseason): build infrastructure
- Oct 8 → Oct 22: preseason validation
- Oct 22: opening night — NBA v1.0 live

## How to apply

When resuming NBA work: start from `nba_data_client.py`. Don't attempt
to resurrect any of the 4 deleted BDL-based scripts — that architecture
is dead. Build fresh mirroring the NHL v1.0 scaffolding pattern
(`nhl_data_client.py`, `nhl_game_context.py`, `nhl_resolve_results.py`,
`nhl_pipeline.yml`).

Related: [[project-nhl-scope]] (parallel pattern),
[[project-nba-offseason-rebuild]] (superseded),
[[project-nba-chalk-trap-fix]] (superseded — legacy chalk-trap gate
was in nba_picks_generator.py, gone now)
