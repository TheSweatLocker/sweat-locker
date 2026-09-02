-- NCAAF AP rank columns (2026-09-02)
-- Powers the ⭐ Ranked matchup badge on game cards.
-- Populated by ncaaf_rankings_pull.py from CFBD /rankings endpoint.

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_ap_rank INT,
  ADD COLUMN IF NOT EXISTS away_ap_rank INT;

COMMENT ON COLUMN public.ncaaf_game_context.home_ap_rank IS
  '2026-09-02 AP poll rank for home team (1-25 or NULL if unranked). Refreshed weekly by ncaaf_rankings_pull.';
COMMENT ON COLUMN public.ncaaf_game_context.away_ap_rank IS
  '2026-09-02 AP poll rank for away team (1-25 or NULL if unranked).';

NOTIFY pgrst, 'reload schema';
