# NHL Buildout — Design + Remaining Work (2026-08-16)

## Status: Foundation shipped, real data + models still needed

**Season targets**: preseason Sep 21 · regular Oct 7. Full production by mid-Oct.

## What's shipped (2026-08-16)

- `supabase/migrations/20260816d_nhl_foundation.sql`
  - `nhl_game_context` — 80+ columns covering teams, goalies, xG stats, market, models
  - `nhl_game_results` — score + OT/SO flags
  - `nhl_props` — player props (same shape as nfl_props / mlb_pipeline_props)
- `mlb_pipeline/seed_nhl_signal_sources.py` — 27 NHL signals seeded across:
  - model (3), goalie (6), shots (3), team_form (5), situational (4), cohort (2), handlers (4)
- `mlb_pipeline/backfill_signal_tiers.py` — NHL added to SPORT_TABLES + `--sport ALL`
- `mlb_pipeline/monitor_ensemble_health.py` — NHL added to SPORT_TABLES
- `mlb_pipeline/ensemble_scorer.py` — already sport-agnostic, NHL routes automatically

## What still needs building (in priority order)

### 1. Data sources (~4h)

**NHL API** (free, no key needed) — `https://api-web.nhle.com/v1/`
- Schedule: `/schedule/{yyyy-mm-dd}` — daily games + confirmed goalies
- Team stats: `/club-stats/{team}/{season}/2` — season averages
- Player stats: `/player/{id}/game-log/{season}/2` — per-game history

**MoneyPuck** (free CSV download) — `https://moneypuck.com/moneypuck/playerData/seasonSummary/YYYY/regular/`
- xGF/60, xGA/60, high-danger, corsi, penalty-kill % — anything not on the NHL API

**Recommendation**: build `mlb_pipeline/nhl_data_client.py` that abstracts both.

### 2. Context enrichment (~4h)

`mlb_pipeline/nhl_game_context.py` — mirrors nfl_game_context structure:
- `pull_schedule(game_date)` — daily NHL API pull
- `enrich_goalies(games)` — starter confirmed + season SV% + L5 SV% + GSAA
- `enrich_team_stats(games)` — MoneyPuck xGF/xGA/high-danger + NHL API PP%/PK%
- `enrich_market(games)` — Odds API pull for ML/puck-line/total
- `enrich_situational(games)` — rest days, back-to-back, road-trip length, travel distance
- `compute_context(games)` — assembles + writes to `nhl_game_context`

### 3. Public split extension (~6h)

Currently OC/FR/Cleatz scrapers support MLB, NFL, NCAAF. Need to:
- Add NHL to `mlb_pipeline/oddscrowd_scraper.py`
- Add NHL to `mlb_pipeline/fadereport_scraper.py`
- Verify Cleatz has NHL coverage (may not — check with `cleatz_scraper.py`)

### 4. Models (~4h)

**Panel projection** (`mlb_pipeline/nhl_panel_projection.py`):
- Aggregate consensus from Odds API + external projection sources
- Writes `panel_pred_total` + `panel_pred_spread`

**MC simulator** (optional, `mlb_pipeline/nhl_mc_simulator.py`):
- Simulate 10k games from team xGF/xGA + goalie SV%
- Writes `mc_probabilities` JSON

**Panel-only is sufficient for v1**. MC is nice-to-have.

### 5. Resolver + backfill (~2h)

`mlb_pipeline/resolve_nhl_results.py`:
- Poll NHL API `/scoreboard/{yyyy-mm-dd}` for final scores
- Write to `nhl_game_results`
- Handle OT/SO flags (regulation OT + shootout affect line grading)

Once results start populating (~2 weeks into season), backfill_signal_tiers
auto-tiers NHL signals from replay. No further code needed.

### 6. Prop pipeline (~4h)

`mlb_pipeline/nhl_generate_props.py` — mirror of nfl_generate_props:
- Pull player prop lines from Odds API
- L5 rolling averages from player game logs
- Opponent defensive adjustment (goals allowed by position?)
- Tier gate based on projected vs line edge

NHL props most-bet:
- Shots on goal (biggest volume)
- Points / assists / goals
- Saves (goalies)
- Blocked shots

### 7. Ensemble cutover (~1h)

Once context table populates, add to `nhl_game_context.py` (like NFL):
```python
from ensemble_scorer import score_game as _ensemble_score
decision = _ensemble_score('NHL', row)
if decision is not None:
    row['primary_play'] = decision.top().to_primary_play_dict()
```

### 8. Cron workflow (~1h)

`.github/workflows/nhl_pipeline.yml` — mirror MLB structure:
- 8am ET daily: schedule pull + market update + context enrichment
- 6pm ET daily: lineup lock + final context + card generation
- Midnight ET daily: resolver + backfill_signal_tiers --sport NHL

## Total remaining scope estimate

**~20-25 hours across 2-3 weeks** to production-ready NHL system with real signals + models + splits + props.

## Fastest path to Week 1 (Oct 7)

Minimum viable (skip nice-to-haves):
1. Data client + schedule/goalie/team-stat enrichment (~6h)
2. Panel projection only, skip MC (~2h)
3. Resolver + backfill (~2h)
4. Ensemble cutover (~1h)
5. Simple daily cron (~1h)

**~12h focused work** = MVP NHL by Sep 21 preseason. Skip splits + props for v1, add v1.5 after season starts and we see what's needed.
