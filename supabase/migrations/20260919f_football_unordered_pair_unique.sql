-- 2026-09-19  One matchup, one row — including when the teams are REVERSED.
--
-- WHY
-- 20260918d added UNIQUE (game_date, home_team, away_team) to
-- ncaaf_game_context. That constraint cannot see an orientation flip:
-- ('Virginia','West Virginia') and ('West Virginia','Virginia') are two
-- different tuples, so both were allowed. On 2026-09-19 both existed for
-- the same game and each carried its own published pick — STRONG
-- 'Virginia -8.5' (conv 77) and LEAN 'West Virginia +10.5' (conv 55).
--
-- The flipped row was not merely redundant, it was WRONG: it priced West
-- Virginia as a -410 home favourite while the correct row priced Virginia
-- -325 at home. The favourite changes teams between the rows, which is
-- what happens when odds are attached by POSITION (the home slot) instead
-- of by team identity. So a flipped row has the line, the moneyline and
-- home-field advantage all backwards.
--
-- Fix: key on the UNORDERED pair. LEAST/GREATEST normalises the two team
-- names so either orientation collides with the other.
--
-- FOOTBALL ONLY, deliberately.
-- Two football teams cannot meet twice on one calendar day, so this is
-- safe there. MLB DOES play doubleheaders — the same two teams, same
-- date, legitimately twice — so the identical index on mlb_game_context
-- would reject real games. MLB is covered by the DUPCTX check in
-- reconcile_resolution.py instead, which was widened to the unordered
-- pair in the same commit and reports rather than blocks.
--
-- Both duplicate rows were removed before this ran (repair verified every
-- dependent row was ungraded first), so the index builds clean.

CREATE UNIQUE INDEX IF NOT EXISTS ncaaf_ctx_unordered_pair_uniq
    ON public.ncaaf_game_context (
        game_date,
        LEAST(home_team, away_team),
        GREATEST(home_team, away_team)
    );

CREATE UNIQUE INDEX IF NOT EXISTS nfl_ctx_unordered_pair_uniq
    ON public.nfl_game_context (
        game_date,
        LEAST(home_team, away_team),
        GREATEST(home_team, away_team)
    );

COMMENT ON INDEX public.ncaaf_ctx_unordered_pair_uniq IS
    'One row per matchup per day regardless of home/away orientation. '
    'The ordered UNIQUE from 20260918d could not catch a reversed pair; '
    'see 20260919f. Football only — MLB doubleheaders make this unsafe there.';
COMMENT ON INDEX public.nfl_ctx_unordered_pair_uniq IS
    'One row per matchup per day regardless of home/away orientation. '
    'See 20260919f.';

NOTIFY pgrst, 'reload schema';
