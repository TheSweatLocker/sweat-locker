-- NCAAF kicking + red-zone efficiency (2026-09-02)
-- Adds columns to ncaaf_team_stats populated by extended ncaaf_stats_pull.

ALTER TABLE public.ncaaf_team_stats
  ADD COLUMN IF NOT EXISTS rz_td_rate NUMERIC,
  ADD COLUMN IF NOT EXISTS rz_score_rate NUMERIC,
  ADD COLUMN IF NOT EXISTS fg_pct NUMERIC,
  ADD COLUMN IF NOT EXISTS opp_rz_td_rate NUMERIC;

COMMENT ON COLUMN public.ncaaf_team_stats.rz_td_rate IS
  '2026-09-02 Red-zone TD rate (TDs / red-zone trips). Higher = finishes drives.';
COMMENT ON COLUMN public.ncaaf_team_stats.rz_score_rate IS
  '2026-09-02 Red-zone score rate (any score / trips). Includes FGs.';
COMMENT ON COLUMN public.ncaaf_team_stats.fg_pct IS
  '2026-09-02 Field goal percentage (kicks made / attempted).';
COMMENT ON COLUMN public.ncaaf_team_stats.opp_rz_td_rate IS
  '2026-09-02 Defense: opponent RZ TD rate. Lower = red-zone D.';

-- Convenience columns on game_context too so Vault Match matches_fn
-- can read directly without joining to team_stats every time.
ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_rz_td_rate NUMERIC,
  ADD COLUMN IF NOT EXISTS away_rz_td_rate NUMERIC,
  ADD COLUMN IF NOT EXISTS home_opp_rz_td_rate NUMERIC,
  ADD COLUMN IF NOT EXISTS away_opp_rz_td_rate NUMERIC;

NOTIFY pgrst, 'reload schema';
