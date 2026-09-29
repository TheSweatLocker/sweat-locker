---
name: project-nhl-rebuild-status-817
description: "2026-08-17 NHL wrap — 1,335 games from 2024-25 backfilled, resolver fixed (schedule endpoint not scoreboard), tendencies verified. Props + card integration remain."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-18T01:27:22.842Z
---

Extends [[project-nhl-scope]]. Full status after 8/17 wrap session.

## SHIPPED end-to-end

- `mlb_pipeline/nhl_data_client.py` — NHL API client. **BUG FIXED**:
  `get_scoreboard()` was hitting `/v1/scoreboard/{date}` which returns
  empty for historical dates. Switched to `/v1/schedule/{date}` which
  returns weekly gameWeek array; filter to target date + OFF/OFFICIAL
  states. Now supports full historical backfill.
- `nhl_game_context.py` — daily context builder (schedule + odds + rest)
- `nhl_resolve_results.py` — daily grader
- `backfill_nhl_team_tendencies.py` — L5 rolling. **Default lookback
  bumped to 730d** to cover current + prior NHL season.
- `backfill_nhl_history.py` — **NEW 8/17**: pulls entire season into
  nhl_game_results. 2024-25 season loaded (1,335 games).
- 15 NHL signals seeded (10 team_form + 2 goalie GSAA + 3 rest/B2B)
- Workflow `nhl_pipeline.yml` — daily 8am + 4pm ET cron

## HISTORICAL DATA now in DB

- **1,335 NHL games** from 2024-25 season in `nhl_game_results`
- home_win populated for all
- spread_result / total_result NULL (no historical NHL odds source
  integrated — Odds API doesn't provide historicals)
- Enables ML-based tendency signals (`home_ml_last5` etc.) to fire on
  real preseason data starting late September

## KNOWN GAPS

**ATS/OU tendencies**: NULL because no historical NHL puckline/total.
- **Impact**: `home_team_ats_hot`, `over_trend`, `covers_as_fav/dog`
  signals won't fire until preseason live odds start flowing (Sept 21+)
- **Signals affected**: 7 of the 15 signals rely on close_puckline data.
  ML-based (`home_ml_last5`, `home_fades_own_ml_hot`) + goalie GSAA +
  B2B/rest all work fine.
- **Fix option**: Pay for historical NHL odds source, or accept the
  ATS/OU signals will only tune during live season.

**International tournament pollution**: Backfill captured Four Nations
Face-Off games with country teams (SWE, FIN). Minor — team_form only
fires when team appears in a current-day nhl_game_context row, and
countries don't play regular season games. Ignorable.

## PENDING (deferred to Sept-Oct)

1. **Goalie props pipeline** — save %, shutouts, saves. Reuse
   `prop_ensemble_scorer.py` architecture. MoneyPuck GSAA data source
   exists in `nhl_data_client.get_goalie_stats()`.
2. **Skater props pipeline** — SOG, points, shots. MoneyPuck skater
   data source needed.
3. **App integration** — Sharp Card fetcher needs `nhl_game_context`
   entry in the fetchSharpTab function (mirrors NFL/NCAAF pattern
   already there).
4. **Card generator** — NHL sweat card / weekly card generator
   (nhl_weekly_card.py mirror of ufc_weekly_card.py).
5. **Signal backtest** — Run 15 signals against 1,335 backfilled games
   to see baseline hit rates before season starts. Auto-promotes any
   VALIDATED signals from DISCOVERY weight (0.20 → 1.0).

## Timeline

- Now → Sept 21 (preseason): infrastructure warms up on cron
- Sept 21-Oct 6: preseason data flows in, signals fire on real games,
  hit rates accrue
- Oct 7: opening night, NHL v1.0 live — sides + totals + ML
- Nov: goalie prop pipeline (once model calibration validated)
- Dec: skater prop pipeline

Related: [[project-nhl-scope]], [[project-nba-rebuild-status-817]]
(parallel pattern)
