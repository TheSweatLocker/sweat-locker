-- Team ATS / O/U / ML tendency columns on mlb_game_context (2026-08-17).
--
-- Activates the 10 ATS/OU signal_sources rows shipped yesterday which
-- reference these fields. Without these columns, condition_expr evaluates
-- ctx.home_ats_last10 → None → signal never fires.
--
-- Populated nightly by backfill_team_tendencies.py after resolver runs.

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS home_ats_last10           INT,
  ADD COLUMN IF NOT EXISTS home_ats_last10_losses    INT,
  ADD COLUMN IF NOT EXISTS away_ats_last10           INT,
  ADD COLUMN IF NOT EXISTS away_ats_last10_losses    INT,
  ADD COLUMN IF NOT EXISTS home_ou_last10_overs      INT,
  ADD COLUMN IF NOT EXISTS home_ou_last10_unders     INT,
  ADD COLUMN IF NOT EXISTS away_ou_last10_overs      INT,
  ADD COLUMN IF NOT EXISTS away_ou_last10_unders     INT,
  ADD COLUMN IF NOT EXISTS home_covers_as_fav_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_covers_as_dog_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_fav_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_dog_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS home_ml_last10            INT,
  ADD COLUMN IF NOT EXISTS home_ml_last10_losses     INT,
  ADD COLUMN IF NOT EXISTS away_ml_last10            INT,
  ADD COLUMN IF NOT EXISTS away_ml_last10_losses     INT,
  ADD COLUMN IF NOT EXISTS team_tendencies_updated_at TIMESTAMPTZ;

NOTIFY pgrst, 'reload schema';
