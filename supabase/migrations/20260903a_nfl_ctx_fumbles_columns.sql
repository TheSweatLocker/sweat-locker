-- 2026-09-03: nfl_game_context missing 44 team-stat columns
--
-- Root cause: nfl_game_context.py writes 88 per-game stat columns
-- but only 44 exist in DB schema. Every NFL context write PGRST204s
-- on the first missing column → 0 rows written all season → LR
-- override never fires. Strip-retry deployed in nfl_game_context.py
-- as belt-and-suspenders; migration is proper fix.

ALTER TABLE public.nfl_game_context
  -- Offensive per-game stats
  ADD COLUMN IF NOT EXISTS home_pass_yds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_pass_yds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_rush_yds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_rush_yds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_pass_tds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_pass_tds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_rush_tds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_rush_tds_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_pass_ints_pg          NUMERIC,
  ADD COLUMN IF NOT EXISTS away_pass_ints_pg          NUMERIC,
  ADD COLUMN IF NOT EXISTS home_pass_epa_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_pass_epa_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_rush_epa_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_rush_epa_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_pass_cpoe             NUMERIC,
  ADD COLUMN IF NOT EXISTS away_pass_cpoe             NUMERIC,
  ADD COLUMN IF NOT EXISTS home_rush_first_downs_pg   NUMERIC,
  ADD COLUMN IF NOT EXISTS away_rush_first_downs_pg   NUMERIC,
  ADD COLUMN IF NOT EXISTS home_sacks_suffered_pg     NUMERIC,
  ADD COLUMN IF NOT EXISTS away_sacks_suffered_pg     NUMERIC,
  ADD COLUMN IF NOT EXISTS home_penalties_pg          NUMERIC,
  ADD COLUMN IF NOT EXISTS away_penalties_pg          NUMERIC,
  ADD COLUMN IF NOT EXISTS home_penalty_yds_pg        NUMERIC,
  ADD COLUMN IF NOT EXISTS away_penalty_yds_pg        NUMERIC,
  -- Defensive per-game stats
  ADD COLUMN IF NOT EXISTS home_def_ppg               NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_ppg               NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_ypg               NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_ypg               NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_pass_ypg          NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_pass_ypg          NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_rush_ypg          NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_rush_ypg          NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_pass_epa_allowed  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_pass_epa_allowed  NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_rush_epa_allowed  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_rush_epa_allowed  NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_sacks_pg          NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_sacks_pg          NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_ints_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_ints_pg           NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_fumbles_pg        NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_fumbles_pg        NUMERIC,
  ADD COLUMN IF NOT EXISTS home_def_tds_pg            NUMERIC,
  ADD COLUMN IF NOT EXISTS away_def_tds_pg            NUMERIC;

NOTIFY pgrst, 'reload schema';
