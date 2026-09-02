-- NCAAF Strength of Record columns (2026-09-02)
-- Powers SoR-based signals + Vault Match patterns. Pulled from CFBD
-- /ratings/sor endpoint by ncaaf_sor_pull.py nightly.

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_sor NUMERIC,
  ADD COLUMN IF NOT EXISTS away_sor NUMERIC;

COMMENT ON COLUMN public.ncaaf_game_context.home_sor IS
  '2026-09-02 Strength of Record rating for home team. Positive = above-average schedule-adjusted record. Refreshed weekly by ncaaf_sor_pull.';
COMMENT ON COLUMN public.ncaaf_game_context.away_sor IS
  '2026-09-02 SoR for away team.';

NOTIFY pgrst, 'reload schema';
