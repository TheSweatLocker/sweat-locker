---
name: project-v11-recency-wrc
description: v1.1 backside task — pull team L7/L14 xwOBA from Baseball Savant (NOT FanGraphs — wRC+ is FG-only and their API is unreliable). Currently using runs/game delta as proxy via offense_drift.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Queued v1.1 task**: replace the runs/game-delta proxy with true L7/L14 team wRC+ from FanGraphs.

**Why**: 2026-05-23 the user asked about recency wRC+ specifically — "Angels feel hot lately, season wRC+ doesn't show it." The existing `offense_drift` field (L10 R/G - season R/G) catches most of this, but R/G has noise (BABIP cluster luck, opponent quality, park effects). Park/league-adjusted wRC+ is the cleaner signal.

**What's shipped today (2026-05-23) as a stopgap**:
- `score_batter_hits` reads `{side}_offense_drift`, adjusts conviction ±6 at drift ≥1.0 or ≤-1.0
- `score_batter_hits_under` does the inverse
- New signal labels `team_heat` / `team_cold` surfaced in prop signals dict (app already renders these)
- Confluence vote on recency was already wired (line 3097+ in game_context.py)

**What's deferred to v1.1 — REVISED to Savant xwOBA path (2026-05-23 PM)**:

**Source decision**: Baseball Savant, NOT FanGraphs. Reasoning:
- FanGraphs has throttled bulk scraping aggressively in last 18 months; works in dev, fails in 6am ET cron
- Savant is MLB-official, stable, no rate-limit games
- Savant doesn't publish wRC+ natively (FanGraphs proprietary) — use **xwOBA** instead
- xwOBA is actually MORE predictive than wRC+ for recency because it strips BABIP cluster luck
- We already have `home_team_xwoba` / `away_team_xwoba` on `mlb_game_context` (populated by `savant_enrichment.py`) — need to extend to L7/L14 windows

**Implementation steps**:
1. Verify current `team_xwoba` is season-wide (not recency) — confirm in savant_enrichment.py
2. Add `mlb_team_offense.xwoba_last7` and `xwoba_last14` columns
3. Extend savant_enrichment.py: pull from baseballsavant.mlb.com/leaderboard/expected_statistics with date range filters (use last 7/14 days as window)
4. Compute `home_xwoba_delta_7d = xwoba_last7 - xwoba_season` per team, store on `mlb_game_context`
5. Replace `offense_drift` reads in `generate_props.py` with xwoba_delta (or keep both — drift is runs-based, xwoba is contact-quality based, they're independent signals)
6. Add xwoba_delta as a confluence vote in `game_context.py` — fire when |delta| >= 0.020 (one std dev of team xwOBA)
7. Surface "🔥 HOT" / "❄️ COLD" badge in scout report when |xwoba_delta_7d| >= 0.020

**Why not tonight (still)**: Savant pull needs date-range filter wiring + new schema columns + nightly cron extension. ~3 hours. Stopgap (offense_drift in prop scoring) addresses 80% of the value.

**Reference URL**: `https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year=2026&team=all&min=1&csv=true` — has date-range support via `month=` param. For true L7/L14, use Statcast search API: `https://baseballsavant.mlb.com/statcast_search/csv?...&game_date_gt={cutoff}` aggregated per team.

**2026-05-23 PM update — Savant approach hit blockers, plan revised again:**

Tested three URL variants on the leaderboard endpoint:
- `&dateStart=2026-05-09&dateEnd=2026-05-23` — IGNORED, returns season
- `&month=5` — IGNORED, returns season (Nationals 2038 PA confirms season-wide)
- `&split=last14` — IGNORED, returns season

Tested statcast_search/csv with `game_date_gt`/`game_date_lt` — TIMES OUT (>30s). The raw play-level CSV is massive for a 14-day window.

**Practical implementation paths from here:**

1. **MLB Stats API team game logs** — pull per-team game log via
   `https://statsapi.mlb.com/api/v1/teams/{id}/stats?stats=byMonth&group=hitting&season=2026`
   or `?stats=lastXGames&group=hitting&numTeams=...`. Doesn't have xwOBA
   (FG/Savant metric) but has wOBA, OPS, runs/game by month or last-N.
   Closest available official data.

2. **Aggregate from our own stored data** — we already store game results.
   Could enrich `mlb_team_offense` with rolling L7/L14 runs scored + OPS
   from `mlb_game_results` aggregation. Updates daily as games resolve.
   Most stable path; no third-party scraping.

3. **Last resort: scrape FanGraphs leaderboard HTML** — needs HTML
   parser, rate-limited, breaks when they redesign. NOT recommended.

**Recommended path: #2 (self-aggregate).** When games close, sum
runs/hits/walks per team for last 7 / last 14 calendar days, derive
OPS from box-score totals, write to `mlb_team_offense.{ops,wrc_proxy}_last7/last14`.
Then build a wrc_proxy = (OPS - league_avg_OPS) * 100 as a poor-man's
recency wRC+ since true wRC+ requires FG park-adjusted calc.

The current `offense_drift` (L10 R/G - season R/G) stays as the
working stopgap until #2 ships.

Related: [[project_offense_drift_signal]], [[project_barrel_and_bb_h_signals]] (queued similarly).
