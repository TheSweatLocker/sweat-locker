-- Team ATS / O/U / ML tendency columns on nfl_game_context (2026-08-17).
--
-- Ports the MLB/NCAAF team_tendencies pattern to NFL. Populated
-- nightly by backfill_nfl_team_tendencies.py after resolver runs.
--
-- Windows: L4 for in-season form (~24% of 17-game season). Season
-- fav/dog splits use trailing-3-season pool (400d lookback) so weeks
-- 1-4 have real numbers from the prior season carrying forward.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS home_ats_last4            INT,
  ADD COLUMN IF NOT EXISTS home_ats_last4_losses     INT,
  ADD COLUMN IF NOT EXISTS away_ats_last4            INT,
  ADD COLUMN IF NOT EXISTS away_ats_last4_losses     INT,
  ADD COLUMN IF NOT EXISTS home_ou_last4_overs       INT,
  ADD COLUMN IF NOT EXISTS home_ou_last4_unders      INT,
  ADD COLUMN IF NOT EXISTS away_ou_last4_overs       INT,
  ADD COLUMN IF NOT EXISTS away_ou_last4_unders      INT,
  ADD COLUMN IF NOT EXISTS home_covers_as_fav_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_covers_as_dog_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_fav_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_dog_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_ml_last4             INT,
  ADD COLUMN IF NOT EXISTS home_ml_last4_losses      INT,
  ADD COLUMN IF NOT EXISTS away_ml_last4             INT,
  ADD COLUMN IF NOT EXISTS away_ml_last4_losses      INT,
  ADD COLUMN IF NOT EXISTS team_tendencies_updated_at TIMESTAMPTZ;

NOTIFY pgrst, 'reload schema';
