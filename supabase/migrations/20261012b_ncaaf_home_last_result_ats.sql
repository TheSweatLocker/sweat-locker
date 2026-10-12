-- 2026-10-12 · home_last_result_ats on ncaaf_game_context.
--
-- WHY. ncaaf_home_dog_letdown_fade has been enabled since it was written
-- and has never been able to fire, because the only column it reads does
-- not exist anywhere in the schema:
--
--   ctx.close_spread > 0                       (NCAAF stores POSITIVE
--                                               close_spread when HOME is
--                                               the DOG)
--   AND ctx.home_last_result_ats == 'W'        <- this
--   -> side AWAY_ML
--
-- i.e. the classic letdown fade: a home dog that covered last week gets
-- faded back. Found by audit_dead_signals.py (2026-10-12), which is the
-- last of the ten football signals that were physically unable to fire.
--
-- The DATA was already there — ncaaf_game_results carries game_date,
-- home_team, away_team and spread_result (12,938 team-game results across
-- 286 teams). load_last_ats_by_team() in ncaaf_game_context.py builds the
-- per-team history and last_ats_result() reads it bounded by the game's
-- OWN date, so it is prior information even on a historical rebuild.
--
-- 'W' means THAT TEAM covered. home_covered is a W for the home side and
-- an L for the away side; storing it per game rather than per team would
-- invert the signal for one of them.
--
-- Four rows in ncaaf_game_results carry 'true'/'false' in spread_result.
-- The loader skips them rather than coercing — a junk value silently read
-- as a cover would put a fabricated result into a published pick.

ALTER TABLE ncaaf_game_context
    ADD COLUMN IF NOT EXISTS home_last_result_ats TEXT;

COMMENT ON COLUMN ncaaf_game_context.home_last_result_ats IS
  'ATS result of the HOME team''s most recent completed game, strictly before this game''s date: W = that team covered, L = did not, P = push, NULL = no prior game. Written by ncaaf_game_context.load_last_ats_by_team(). Read by ncaaf_home_dog_letdown_fade.';

NOTIFY pgrst, 'reload schema';
