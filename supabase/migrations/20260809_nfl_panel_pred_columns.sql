-- Add Panel projection columns to nfl_game_context (2026-08-09).
--
-- These are populated by nfl_game_context.py during context build via
-- nfl_panel_projection.compute_panel_projection(). Separate from
-- model_pred_* (matchup-EPA model) so downstream systems can see BOTH
-- and confluence engine can weigh them as two independent votes.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS panel_pred_home_pts NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS panel_pred_away_pts NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS panel_pred_total NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS panel_confidence NUMERIC(4,2),      -- 0-1 based on players used
  ADD COLUMN IF NOT EXISTS panel_source TEXT,                   -- 'sleeper' / 'espn_fantasy' / 'ensemble'
  ADD COLUMN IF NOT EXISTS panel_players_used INT,
  ADD COLUMN IF NOT EXISTS panel_injury_outs INT;

NOTIFY pgrst, 'reload schema';
