---
name: project-elo-models-shipped-817
description: 2026-08-17 Elo models shipped for NBA + NHL. Simple self-training ratings + market-edge signals. Ready for season openers.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-18T02:21:21.196Z
---

## Shipped

**NBA Elo (`mlb_pipeline/nba_elo.py`):**
- Home advantage +100 Elo, K=20, 25 Elo/pt spread conversion
- Total projection: avg of team pts_for + pts_against
- Wired into `nba_game_context.py::enrich_elo()`

**NHL Elo (`mlb_pipeline/nhl_elo.py`):**
- Home advantage +60 Elo, K=6 (small — NHL variance high)
- OT/SO games get 0.5×K
- **Trained on 1,335 games from 2024-25 season.** Top 5: Winnipeg (1557.8),
  Toronto (1542.8), Washington (1539.3), Vegas (1537.2), Dallas (1533.5).
  Bottom: San Jose (1407.1) — matches 2024-25 reality.
- Wired into `nhl_game_context.py::enrich_nhl_elo()`

## 11 new model signals

**NBA (6):** edge_home_spread/edge_away_spread, home_ml_strong/away_ml_strong,
over_edge/under_edge

**NHL (5):** home_ml_strong/away_ml_strong (58% threshold), over_edge/under_edge
(0.6-goal threshold), value_dog (market prices dog 40pts worse than Elo)

## Sign bug caught + fixed

NHL predict had inverted ML sign on favorites (+299 instead of -299).
Fixed to direct formula.

## NBA backfill

`backfill_nba_history.py` runs cleanly on GHA. Local dev hit ESPN
connection resets — added retry + backoff. Will populate 2024-25 season
on next GHA cron.

## Migrations pending user apply

1. `20260817_nba_foundation.sql`
2. `20260817_nba_model_signals.sql`
3. `20260817_nhl_model_signals.sql`

Related: [[project-nba-rebuild-status-817]], [[project-nhl-rebuild-status-817]]
