-- 2026-06-04 — Decoupled line poller architecture.
--
-- Problem: line state is only captured at morning/afternoon cron passes.
-- close_total is unreliable (4/9 games missing tonight). No visibility
-- into line movement during the day. The afternoon cron flipped v3/v4
-- on multiple games AFTER the user posted tonight's card — first time the
-- shift was visible was when I happened to re-query, not at compute time.
--
-- Fix: dedicated mlb_line_history table populated by a poll-only process
-- (line_poller.py, runs every 15 min independent of main cron). Stores
-- the full time series. Derived columns on mlb_game_context expose the
-- current state to the scorer + audit:
--   current_total — latest observed line
--   current_total_at — when it was observed
--   line_movement — current_total - open_total (positive = market drifted UP)
--   line_movements_count — how many distinct line values seen today
--   close_total — the line at first pitch (set by poller when game state
--                 transitions to "In Progress")

CREATE TABLE IF NOT EXISTS mlb_line_history (
    id BIGSERIAL PRIMARY KEY,
    game_id TEXT NOT NULL,
    game_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Total
    total_line NUMERIC,
    over_odds INTEGER,
    under_odds INTEGER,
    -- Spread (run line)
    spread NUMERIC,
    spread_home_odds INTEGER,
    spread_away_odds INTEGER,
    -- Moneyline
    home_ml INTEGER,
    away_ml INTEGER,
    -- Source
    source_book TEXT,
    -- Index for time-series queries per game
    UNIQUE (game_id, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_line_history_game_date
    ON mlb_line_history (game_date, game_id, fetched_at DESC);

COMMENT ON TABLE mlb_line_history IS
  'Time series of MLB betting lines per game. Populated every 15 minutes by line_poller.py independent of the main cron. Powers line-movement visualization, close_total reliability, and decoupled-from-cron line state.';

-- Derived columns on mlb_game_context (point-in-time current state).
ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS current_total NUMERIC,
    ADD COLUMN IF NOT EXISTS current_total_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS line_movement NUMERIC,
    ADD COLUMN IF NOT EXISTS line_movements_count INTEGER DEFAULT 0;

COMMENT ON COLUMN mlb_game_context.current_total IS
  'Most recently observed total line per the line poller. Used by the scorer/gate logic instead of close_total when close_total is NULL (mid-day) so the conflict gates run on current data, not morning data.';

COMMENT ON COLUMN mlb_game_context.line_movement IS
  'current_total - open_total. Positive = line moved UP since first observation. Negative = moved DOWN. Confluence signal: model agrees with movement direction = stronger; disagrees = yellow flag.';

NOTIFY pgrst, 'reload schema';
