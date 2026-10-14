-- ════════════════════════════════════════════════════════════════════
-- 20261014a — a sanctioned repair path for a CORRUPTED opening line
--
-- THE PROBLEM
-- 20260917h_opening_line_freeze.sql added freeze_opening_lines(), a BEFORE
-- UPDATE trigger that preserves any non-NULL open_* value. That is correct and
-- should stay: the odds pullers rebuild a whole row dict and set open_* to the
-- CURRENT price on every upsert, so without the trigger the true opener is
-- overwritten by today's close and the Line Movement strip reads flat.
--
-- But the trigger fires unconditionally, which makes a BAD opener immortal.
-- There is no value you can UPDATE it to — not even NULL — because the
-- function always assigns NEW := OLD when OLD is non-NULL. And the UPDATE
-- reports success: PostgREST returns 200, and with return=representation it
-- hands back the OLD value, so a caller that does not diff the response
-- believes the write landed.
--
-- Measured case, NFL CHI @ CAR 2026-09-13: context held
--     open_total = 96.5   open_spread = -21.5
-- The game finished 59-37 — 96 points, margin 22 — so both "openers" were the
-- final score offset by half a point. nfl_game_results held the correct 47.5
-- and -3.0. close_* was repairable; open_* was not, and watchdog_market_sanity
-- still reports it as REFUSED.
--
-- WHY NOT JUST LET NULL THROUGH
-- Allowing `NEW.open_x IS NULL` to pass would give a two-step repair
-- (NULL it, then set it) without any new surface area. Rejected: PostgREST
-- batch upserts UNION their keys across rows, so a batch where only some rows
-- carry open_* sends `open_total: null` for the rest
-- (feedback_postgrest_batch_normalize_keys). That would turn an accidental
-- key union into a silent mass erase of real openers — a worse failure than
-- the one being fixed.
--
-- THE APPROACH
-- An explicit SECURITY DEFINER function with a function-scoped
-- `SET sweat.allow_open_override = on`. The trigger honours that GUC and
-- nothing else sets it, so the ONLY way to change an opener is to call this
-- by name, one row and one table at a time. Callable over PostgREST as
-- /rest/v1/rpc/repair_opening_line.
--
-- ROLLBACK: restore freeze_opening_lines() from 20260917h and
--           DROP FUNCTION public.repair_opening_line.
-- ════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.freeze_opening_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The one escape hatch. Set only by repair_opening_line() below, for the
    -- duration of that call. current_setting(..., true) returns NULL rather
    -- than raising when the GUC was never set, which is the normal path.
    IF coalesce(current_setting('sweat.allow_open_override', true), 'off')
       = 'on' THEN
        RETURN NEW;
    END IF;

    IF OLD.open_home_ml IS NOT NULL THEN
        NEW.open_home_ml := OLD.open_home_ml;
    END IF;
    IF OLD.open_away_ml IS NOT NULL THEN
        NEW.open_away_ml := OLD.open_away_ml;
    END IF;
    IF OLD.open_spread IS NOT NULL THEN
        NEW.open_spread := OLD.open_spread;
    END IF;
    IF OLD.open_total IS NOT NULL THEN
        NEW.open_total := OLD.open_total;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.freeze_opening_lines() IS
    'Preserves non-NULL open_* columns against the odds pullers, which rewrite '
    'them on every upsert. Bypassed only while sweat.allow_open_override = on, '
    'which repair_opening_line() sets for the duration of one call.';


CREATE OR REPLACE FUNCTION public.repair_opening_line(
    p_table      text,
    p_game_id    text,
    p_open_spread      real DEFAULT NULL,
    p_open_total       real DEFAULT NULL,
    p_open_home_ml     integer DEFAULT NULL,
    p_open_away_ml     integer DEFAULT NULL
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
-- Function-scoped so the override cannot leak past this call, even on error.
SET sweat.allow_open_override = 'on'
AS $$
DECLARE
    v_allowed text[] := ARRAY['mlb_game_context', 'nfl_game_context',
                              'ncaaf_game_context', 'nhl_game_context',
                              'nba_game_context', 'ncaab_game_context'];
    v_n integer;
BEGIN
    -- Whitelist, not interpolation. p_table reaches a format() either way, so
    -- it must be proven to be one of ours before it gets there.
    IF NOT (p_table = ANY(v_allowed)) THEN
        RAISE EXCEPTION 'repair_opening_line: % is not a context table', p_table;
    END IF;
    IF p_game_id IS NULL OR length(p_game_id) = 0 THEN
        RAISE EXCEPTION 'repair_opening_line: game_id is required';
    END IF;

    -- COALESCE per column: a NULL argument means "leave this one alone",
    -- so one call can fix just the total without disturbing the spread.
    EXECUTE format(
        'UPDATE public.%I SET '
        '  open_spread  = coalesce($1, open_spread), '
        '  open_total   = coalesce($2, open_total), '
        '  open_home_ml = coalesce($3, open_home_ml), '
        '  open_away_ml = coalesce($4, open_away_ml) '
        'WHERE game_id = $5', p_table)
    USING p_open_spread, p_open_total, p_open_home_ml, p_open_away_ml, p_game_id;

    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END;
$$;

COMMENT ON FUNCTION public.repair_opening_line(text, text, real, real, integer, integer) IS
    'The only sanctioned way to change a stored opening line. One table, one '
    'game_id, NULL args left untouched. Returns rows affected — 0 means the '
    'game_id did not match, which a caller must treat as a failure.';

REVOKE ALL ON FUNCTION public.repair_opening_line(text, text, real, real, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.repair_opening_line(text, text, real, real, integer, integer) TO service_role;

NOTIFY pgrst, 'reload schema';
