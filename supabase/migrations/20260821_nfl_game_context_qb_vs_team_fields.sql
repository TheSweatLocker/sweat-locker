-- Add qb_vs_team_* fields to nfl_game_context so the QB-vs-defense signals
-- shipped 8/21 can actually fire.
--
-- Pattern mirrors mlb_game_context (home_pitcher_vs_team_*). At ctx build time,
-- nfl_game_context.py will look up starting QB for each team, JOIN nfl_qb_vs_team,
-- and populate these fields.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS home_qb_name              TEXT,
  ADD COLUMN IF NOT EXISTS away_qb_name              TEXT,
  ADD COLUMN IF NOT EXISTS home_qb_id                TEXT,
  ADD COLUMN IF NOT EXISTS away_qb_id                TEXT,
  -- Home QB vs away defense (career)
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_career_starts     INT,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_career_qb_rating  NUMERIC,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_career_yds_per_att NUMERIC,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_career_cmp_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_career_td_int_ratio NUMERIC,
  -- Home QB vs away defense (recent 3)
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_recent_n_starts   INT,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_recent_pass_yds_avg NUMERIC,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_recent_pass_td_avg  NUMERIC,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_recent_int_avg    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_qb_vs_team_recent_qb_rating  NUMERIC,
  -- Away QB vs home defense (career)
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_career_starts     INT,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_career_qb_rating  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_career_yds_per_att NUMERIC,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_career_cmp_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_career_td_int_ratio NUMERIC,
  -- Away QB vs home defense (recent 3)
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_recent_n_starts   INT,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_recent_pass_yds_avg NUMERIC,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_recent_pass_td_avg  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_recent_int_avg    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_qb_vs_team_recent_qb_rating  NUMERIC;

COMMENT ON COLUMN public.nfl_game_context.home_qb_vs_team_career_qb_rating IS
  'Home starting QB career passer rating vs the away team defense. Populated by nfl_game_context.py at build time from nfl_qb_vs_team table (backfilled 8/21 with 5 seasons nflverse data).';

NOTIFY pgrst, 'reload schema';
