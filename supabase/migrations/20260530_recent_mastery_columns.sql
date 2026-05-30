-- 2026-05-30 — Recent (L3-start) pitcher vs team mastery columns.
--
-- Career mastery (pitcher_vs_team_era over 5 seasons w/ 15-IP gate) captures
-- whether the pitcher has historically owned or struggled vs this opp. But
-- pitchers change — recent (last 3 starts vs opp) captures CURRENT form.
-- When career and recent diverge significantly, Jerry blends toward recent.
--
-- Per user direction 5/30. Same source (MLB Stats API gameLog) as career
-- mastery but filtered to most recent 3 starts vs this opp specifically.
-- 10-IP minimum gate (3 starts × 3-5 IP each → 10 IP floor catches noise).

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_recent_era NUMERIC,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_recent_era NUMERIC,
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_recent_ip NUMERIC,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_recent_ip NUMERIC,
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_recent_baa NUMERIC,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_recent_baa NUMERIC,
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_recent_n_starts SMALLINT,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_recent_n_starts SMALLINT;

ALTER TABLE mlb_game_results
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_recent_era NUMERIC,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_recent_era NUMERIC,
    ADD COLUMN IF NOT EXISTS home_pitcher_vs_team_recent_ip NUMERIC,
    ADD COLUMN IF NOT EXISTS away_pitcher_vs_team_recent_ip NUMERIC;

COMMENT ON COLUMN mlb_game_context.home_pitcher_vs_team_recent_era IS
  'Home pitcher ERA over LAST 3 starts vs this specific opponent. Different
   from career pitcher_vs_team_era (5 seasons / 15-IP gate). Jerry blends
   toward recent when it disagrees with career by >=2.0 ERA AND recent has
   >=10 IP. Captures pitcher current-form change against this specific opp.';

NOTIFY pgrst, 'reload schema';
