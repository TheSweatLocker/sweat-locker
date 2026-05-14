-- mlb_game_context: sweat_score + sweat_tier columns (2026-05-13)
-- ============================================================
-- Server-authoritative Sweat Score. Previously the app computed its own
-- calcGameSweatScore which used a different formula than play_of_day.py's
-- score_mlb_game — same game routinely showed 60 in the app while the cron
-- computed 72 (12-point gap). Users effectively never saw PRIME because
-- the client formula topped out ~65 on even the strongest spots.
--
-- Fix: server is the single source of truth. play_of_day.py writes the
-- score it computes for every game (not just the POTD) back to mlb_game_context.
-- The app reads mlb_game_context.sweat_score directly and skips the client
-- calc for MLB. Audit cohorts (confluence_prime_ge4 75% etc.) now align
-- with what users actually see in-app.
--
-- Apply via Supabase SQL editor.

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS sweat_score INTEGER,
  ADD COLUMN IF NOT EXISTS sweat_tier  TEXT;

CREATE INDEX IF NOT EXISTS idx_mlb_game_context_sweat_score
  ON mlb_game_context(game_date, sweat_score DESC NULLS LAST);
