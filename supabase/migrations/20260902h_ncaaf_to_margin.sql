-- NCAAF turnover margin columns (2026-09-02)
-- Powers turnover luck regression signal + Vault Match TO-lucky FADE.
-- Populated by ncaaf_to_margin_pull.py from ncaaf_team_stats.

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_to_margin INT,
  ADD COLUMN IF NOT EXISTS away_to_margin INT;

COMMENT ON COLUMN public.ncaaf_game_context.home_to_margin IS
  '2026-09-02 Season turnover margin for home team (takeaways - giveaways). Positive = defense generating > offense giving up. Extreme values regress toward mean.';
COMMENT ON COLUMN public.ncaaf_game_context.away_to_margin IS
  '2026-09-02 Season turnover margin for away team.';

NOTIFY pgrst, 'reload schema';
