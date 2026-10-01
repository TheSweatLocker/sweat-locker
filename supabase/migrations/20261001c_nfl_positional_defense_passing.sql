-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-01c · nfl_positional_defense — passing allowed
--
-- 20261001b aggregated rushing and receiving only, so POSDEF_METRIC in
-- nfl_generate_props could map six markets and had to leave every PASSING
-- market (pass_yds, pass_tds, pass_attempts, pass_completions,
-- pass_interceptions) on the old team-wide def_pass_epa_allowed rank. That was
-- the honest call at the time — claiming a passing-allowed column that did not
-- exist would have been worse than falling back — but it leaves QB props on the
-- weaker lens.
--
-- nfl_player_stats already carries passing_yards, passing_tds, attempts,
-- completions and interceptions beside opponent_team, so these are the same
-- pure aggregation as the rushing/receiving columns. No new feed.
--
-- WHY INTERCEPTIONS MATTER SEPARATELY: pass_interceptions is the one market
-- where a defence FORCING the stat is good for the OVER. Everything else here
-- is "what the defence concedes". The metric is still "what happened against
-- this defence", so the orientation in positional_opp_pct stays correct —
-- higher means more interceptions thrown against them — but anyone reading
-- these columns should keep that asymmetry in mind.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.nfl_positional_defense
  ADD COLUMN IF NOT EXISTS pass_yds_pg          numeric,
  ADD COLUMN IF NOT EXISTS pass_td_pg           numeric,
  ADD COLUMN IF NOT EXISTS pass_att_pg          numeric,
  ADD COLUMN IF NOT EXISTS pass_cmp_pg          numeric,
  ADD COLUMN IF NOT EXISTS pass_int_pg          numeric,
  ADD COLUMN IF NOT EXISTS pass_yds_pg_blended  numeric,
  ADD COLUMN IF NOT EXISTS pass_td_pg_blended   numeric,
  ADD COLUMN IF NOT EXISTS pass_int_pg_blended  numeric;

COMMENT ON COLUMN public.nfl_positional_defense.pass_yds_pg_blended IS
  'Passing yards allowed per game to this position (QB rows carry the signal), '
  'shrunk toward the prior season. Read the blended column.';
COMMENT ON COLUMN public.nfl_positional_defense.pass_int_pg_blended IS
  'Interceptions thrown AGAINST this defence per game — note the asymmetry: '
  'a high value means the defence FORCES picks, which is good for an INT OVER, '
  'whereas every other column here is a vulnerability.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY
-- ════════════════════════════════════════════════════════════════════════
-- SELECT team, games,
--        round(pass_yds_pg,1)         AS pass_yds_raw,
--        round(pass_yds_pg_blended,1) AS pass_yds_blended,
--        round(pass_td_pg_blended,2)  AS pass_td_blended,
--        round(pass_int_pg_blended,2) AS pass_int_blended
--   FROM public.nfl_positional_defense
--  WHERE season = 2026 AND position = 'QB'
--  ORDER BY pass_yds_pg_blended DESC NULLS LAST LIMIT 10;
