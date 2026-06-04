# 2026-06-04 — Combined migration block to paste into Supabase SQL editor

Paste this single block in Supabase SQL editor (Run once). Idempotent —
safe to re-run.

```sql
-- 1. primary_play staleness tracking
ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS primary_play_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN mlb_game_context.primary_play_computed_at IS
  'When primary_play was last written by compute_primary_play. App-side stale-check suppresses rendering when older than 4 hours.';

-- 2. Line history table for decoupled line poller
CREATE TABLE IF NOT EXISTS mlb_line_history (
    id BIGSERIAL PRIMARY KEY,
    game_id TEXT NOT NULL,
    game_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_line NUMERIC,
    over_odds INTEGER,
    under_odds INTEGER,
    spread NUMERIC,
    spread_home_odds INTEGER,
    spread_away_odds INTEGER,
    home_ml INTEGER,
    away_ml INTEGER,
    source_book TEXT,
    UNIQUE (game_id, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_line_history_game_date
    ON mlb_line_history (game_date, game_id, fetched_at DESC);

COMMENT ON TABLE mlb_line_history IS
  'Time series of MLB betting lines per game. Populated every 15 minutes by line_poller.py.';

-- 3. Derived current-state columns on mlb_game_context
ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS current_total NUMERIC,
    ADD COLUMN IF NOT EXISTS current_total_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS line_movement NUMERIC,
    ADD COLUMN IF NOT EXISTS line_movements_count INTEGER DEFAULT 0;

COMMENT ON COLUMN mlb_game_context.current_total IS
  'Most recently observed total line per the line poller. Scorer can use this instead of close_total when close_total is NULL.';

COMMENT ON COLUMN mlb_game_context.line_movement IS
  'current_total - open_total. Positive = market drifted UP.';

NOTIFY pgrst, 'reload schema';
```

After applying, schedule the line poller to run every 15 minutes:

```
*/15 * * * * cd /path/to/sweat-locker/mlb_pipeline && python line_poller.py
```

(or via GitHub Actions / Render cron / whatever scheduler is in use)
