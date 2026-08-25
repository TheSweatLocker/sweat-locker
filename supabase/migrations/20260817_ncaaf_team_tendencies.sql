-- Team ATS / O/U / ML tendency columns on ncaaf_game_context (2026-08-17).
--
-- Ports the MLB team_tendencies pattern to NCAAF ahead of the 2026-08-22
-- season opener. Populated nightly by backfill_ncaaf_team_tendencies.py
-- after resolver runs.
--
-- Windows: L5 for in-season form (~40% of 12-game season). Signals may
-- return None for the first 5 weeks; ensemble handles None gracefully.
-- Season fav/dog splits use full prior-season pool once available.

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_ats_last5            INT,
  ADD COLUMN IF NOT EXISTS home_ats_last5_losses     INT,
  ADD COLUMN IF NOT EXISTS away_ats_last5            INT,
  ADD COLUMN IF NOT EXISTS away_ats_last5_losses     INT,
  ADD COLUMN IF NOT EXISTS home_ou_last5_overs       INT,
  ADD COLUMN IF NOT EXISTS home_ou_last5_unders      INT,
  ADD COLUMN IF NOT EXISTS away_ou_last5_overs       INT,
  ADD COLUMN IF NOT EXISTS away_ou_last5_unders      INT,
  ADD COLUMN IF NOT EXISTS home_covers_as_fav_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_covers_as_dog_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_fav_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_dog_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_ml_last5             INT,
  ADD COLUMN IF NOT EXISTS home_ml_last5_losses      INT,
  ADD COLUMN IF NOT EXISTS away_ml_last5             INT,
  ADD COLUMN IF NOT EXISTS away_ml_last5_losses      INT,
  ADD COLUMN IF NOT EXISTS team_tendencies_updated_at TIMESTAMPTZ;

NOTIFY pgrst, 'reload schema';
