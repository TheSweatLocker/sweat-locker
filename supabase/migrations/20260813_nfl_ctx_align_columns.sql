-- NFL game context — align_status column parity (2026-08-13).
--
-- compute_align_status_nfl.py (which writes oddscrowd_snapshot back to
-- nfl_game_context) uses a shared query in align_status_common.py that
-- selects: close_total, close_spread, open_total, current_total,
-- home_ml_open, home_ml_close, home_ml_odds.
--
-- nfl_game_context already had close_total / close_spread / open_total.
-- The other four columns existed on mlb_game_context but never made it
-- into the NFL context table. Result: PostgREST 400 on the SELECT →
-- compute_align_status_nfl returned "0 games" every run → oddscrowd_snapshot
-- never populated for NFL games (verified on 3 preseason games tonight).
--
-- Fix: add the missing 5 columns as nullable so the shared compute path
-- runs cleanly. NFL doesn't populate them yet (pipeline doesn't track
-- opening ML lines or mid-day movement snapshots), but the query stops
-- failing and oddscrowd_snapshot writes complete.
--
-- Populating current_total / home_ml_open/close for RLM detection on
-- NFL is deferred to the next NFL pipeline sprint — this migration
-- unblocks the immediate NFL oddscrowd flow.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS current_total   NUMERIC,
  ADD COLUMN IF NOT EXISTS home_ml_open    INT,
  ADD COLUMN IF NOT EXISTS home_ml_close   INT,
  ADD COLUMN IF NOT EXISTS home_ml_odds    INT,
  ADD COLUMN IF NOT EXISTS away_ml_odds    INT;

NOTIFY pgrst, 'reload schema';
