-- 20260921e — nhl_game_context.sweat_tier_current
--
-- nhl_game_context was given sweat_score / sweat_tier but not
-- sweat_tier_current, which nba_game_context received in 20260921a. So
-- every NHL context write logs:
--
--   ⚠ nhl_game_context: stripped missing cols (sweat_tier_current)
--     — schema lag, apply pending migrations
--
-- and the value is silently dropped. The writer's strip-and-warn is the
-- right behaviour (it degrades instead of failing the whole run, and it
-- says so out loud, which is how this was caught), but the column should
-- exist.
--
-- sweat_tier_current is the tier as of the LATEST score, distinct from
-- sweat_tier which is the tier at first publish — the pair is what lets a
-- surface show that a play has strengthened or faded since it went up,
-- rather than quietly rewriting history.

ALTER TABLE nhl_game_context
    ADD COLUMN IF NOT EXISTS sweat_tier_current TEXT;

COMMENT ON COLUMN nhl_game_context.sweat_tier_current IS
    'Sweat tier as of the most recent scoring pass. sweat_tier holds the '
    'tier at first publish; this one moves. Matches nba_game_context.';

NOTIFY pgrst, 'reload schema';
