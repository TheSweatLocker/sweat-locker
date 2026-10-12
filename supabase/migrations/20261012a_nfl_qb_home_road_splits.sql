-- 2026-10-12 · QB career home/road split columns on nfl_game_context.
--
-- WHY. Four signals have been enabled since they were written and have
-- NEVER been able to fire, because every column they read is absent:
--
--   nfl_home_qb_home_dominant     home_qb_career_h_r_delta_pp,
--                                 home_qb_career_home_starts
--   nfl_home_qb_recent_home_hot   home_qb_recent_home_starts,
--                                 home_qb_recent_home_win_pct
--   nfl_away_qb_road_struggles    away_qb_career_h_r_delta_pp,
--                                 away_qb_career_road_starts
--   nfl_away_qb_road_inverse      away_qb_career_h_r_delta_pp,
--                                 away_qb_career_road_starts
--
-- They sat in signal_sources as "enabled", counting toward our signal
-- depth, while being physically incapable of contributing. Found by
-- audit_dead_signals.py (2026-10-12).
--
-- The DATA was already there: nfl_game_results carries home_qb_name,
-- away_qb_name and both scores back to 2020. load_qb_home_road_splits()
-- in nfl_game_context.py computes the splits (135 QBs) and the writer
-- already populates these names — it currently reports
-- "stripped missing cols … schema lag" and drops them, which is why this
-- migration is the only thing standing between those signals and firing.
--
-- UNITS, because the two families are NOT the same and the signal
-- thresholds depend on it:
--   *_h_r_delta_pp        PERCENTAGE POINTS  (home win% - road win%),
--                         tested against +/-10.0 and +/-15.0
--   *_recent_home_win_pct FRACTION 0..1, tested against 0.65
-- Storing a fraction where points are expected would leave every one of
-- these conditions false again.

ALTER TABLE nfl_game_context
    ADD COLUMN IF NOT EXISTS home_qb_career_home_starts  INTEGER,
    ADD COLUMN IF NOT EXISTS home_qb_career_h_r_delta_pp NUMERIC,
    ADD COLUMN IF NOT EXISTS home_qb_recent_home_starts  INTEGER,
    ADD COLUMN IF NOT EXISTS home_qb_recent_home_win_pct NUMERIC,
    ADD COLUMN IF NOT EXISTS away_qb_career_road_starts  INTEGER,
    ADD COLUMN IF NOT EXISTS away_qb_career_h_r_delta_pp NUMERIC;

COMMENT ON COLUMN nfl_game_context.home_qb_career_h_r_delta_pp IS
  'Home QB career home win% minus road win%, in PERCENTAGE POINTS (e.g. +34.9). Computed from nfl_game_results by load_qb_home_road_splits(). Prior games only — this enriches upcoming games, so there is no leak; any historical backfill must bound by the row''s own game_date.';
COMMENT ON COLUMN nfl_game_context.home_qb_recent_home_win_pct IS
  'Home QB home win rate over the last 2 seasons, as a FRACTION 0..1 (not points) — nfl_home_qb_recent_home_hot tests >= 0.65.';
COMMENT ON COLUMN nfl_game_context.away_qb_career_h_r_delta_pp IS
  'Away QB career home win% minus road win%, in PERCENTAGE POINTS. nfl_away_qb_road_struggles tests >= +15.0; nfl_away_qb_road_inverse tests <= -10.0.';

-- PostgREST caches the schema; without this the new columns 400 on select
-- and the writer keeps stripping them.
NOTIFY pgrst, 'reload schema';
