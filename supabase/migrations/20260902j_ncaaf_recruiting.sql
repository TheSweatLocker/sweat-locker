-- NCAAF recruiting composite (2026-09-02)
-- CFBD /talent endpoint returns rolling 4-year recruiting talent
-- composite per team. Powers "recruiting composite mismatch" signal
-- (team over/underperforming their roster talent expectation).

ALTER TABLE public.ncaaf_team_stats
  ADD COLUMN IF NOT EXISTS talent_composite NUMERIC;

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_talent NUMERIC,
  ADD COLUMN IF NOT EXISTS away_talent NUMERIC;

COMMENT ON COLUMN public.ncaaf_team_stats.talent_composite IS
  '2026-09-02 CFBD rolling 4-year recruiting composite. Blue-chip teams typically 900+, mid-tier 600-800, group-of-5 400-600.';

NOTIFY pgrst, 'reload schema';
