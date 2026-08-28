-- Atomic POTD write — prevents cache/history divergence from concurrent
-- play_of_day.py invocations (documented 8/22 SDP/NRFI incident).
--
-- Old flow: play_of_day.py made two separate HTTP requests:
--   1. POST jerry_cache (upsert on game_id+sport)
--   2. POST daily_best_bet_history (upsert on bet_date)
-- Race window between 1 and 2 → cache and history could disagree if two
-- crons overlapped.
--
-- New flow: play_of_day.py calls this single RPC. Both upserts happen in
-- one transaction. Concurrent invocations still race but the LAST one
-- wins consistently across both tables.

CREATE OR REPLACE FUNCTION write_potd_atomic(
    p_today          DATE,
    p_sport          TEXT,
    p_narrative      TEXT,
    p_data           JSONB,
    p_game           TEXT,
    p_lean           TEXT,
    p_sweat_score    NUMERIC,
    p_odds_american  NUMERIC DEFAULT NULL
) RETURNS VOID AS $$
DECLARE
    v_key TEXT := 'best_bet_' || p_today::text;
BEGIN
    -- 1. Upsert jerry_cache best_bet_{today}
    INSERT INTO jerry_cache (cache_key, game_id, sport, narrative, data, fetched_at)
    VALUES (v_key, v_key, p_sport, p_narrative, p_data, NOW())
    ON CONFLICT (game_id, sport) DO UPDATE SET
        narrative  = EXCLUDED.narrative,
        data       = EXCLUDED.data,
        fetched_at = EXCLUDED.fetched_at;

    -- 2. Upsert daily_best_bet_history — SAME TRANSACTION as cache
    INSERT INTO daily_best_bet_history (
        bet_date, sport, game, lean, sweat_score, result, odds_american
    ) VALUES (
        p_today, p_sport, p_game, p_lean, p_sweat_score, 'Pending', p_odds_american
    )
    ON CONFLICT (bet_date) DO UPDATE SET
        sport         = EXCLUDED.sport,
        game          = EXCLUDED.game,
        lean          = EXCLUDED.lean,
        sweat_score   = EXCLUDED.sweat_score,
        odds_american = COALESCE(EXCLUDED.odds_american, daily_best_bet_history.odds_american);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

GRANT EXECUTE ON FUNCTION write_potd_atomic TO service_role;

NOTIFY pgrst, 'reload schema';
