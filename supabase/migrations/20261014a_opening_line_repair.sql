-- ════════════════════════════════════════════════════════════════════
-- 20261014a — a sanctioned repair path for a CORRUPTED opening line
--
-- THE PROBLEM
-- freeze_opening_lines() (20260917h, rewritten by 20260919b) is a BEFORE
-- UPDATE trigger on every *_game_context table that preserves any open-line
-- column already holding a value. That is correct and must stay: the odds
-- pullers rebuild a whole row dict and set the open_* columns to the CURRENT
-- price on every upsert, so without it the true opener is overwritten by
-- today's close and the Line Movement strip reads flat.
--
-- But it fires unconditionally, which makes a BAD opener immortal. There is no
-- value you can UPDATE it to — not even NULL — because the function always
-- restores the old value. And the UPDATE reports success: PostgREST returns
-- 200, and with return=representation it hands back the OLD value, so a caller
-- that does not diff the response believes the write landed.
--
-- Measured case, NFL CHI @ CAR 2026-09-13: context held
--     open_total = 96.5   open_spread = -21.5
-- The game finished 59-37 — 96 points, margin 22 — so both "openers" were the
-- final score offset by half a point. nfl_game_results held the correct 47.5
-- and -3.0. close_* was repairable; open_* was not.
--
-- ══ TWO EARLIER DRAFTS OF THIS FILE WERE WRONG. BOTH MATTER. ══
--
-- (1) A custom GUC. The bypass was a function-scoped
--     `SET sweat.allow_open_override = 'on'`. It does not install on Supabase:
--         ERROR: 42501: permission denied to set parameter "sweat.allow_open_override"
--     A function-scoped SET is validated at CREATE time and needs the right to
--     set that parameter; for a custom placeholder GUC that means superuser, or
--     a GRANT SET ON PARAMETER which itself needs superuser. The Supabase
--     `postgres` role is deliberately not superuser, so the mechanism is simply
--     unavailable here. (`SET search_path`, used below, is a USERSET parameter
--     and installs fine — the distinction is the parameter, not the syntax.)
--
-- (2) IT REBASED ONTO THE WRONG FUNCTION BODY. The second draft copied the
--     column-by-column body from 20260917h and would have REVERTED 20260919b,
--     whose header records what that costs:
--         {"code":"42703","message":"record \"old\" has no field \"open_home_ml\""}
--     Every UPDATE to mlb_game_context failed from 9/17 to 9/19 because the
--     sports disagree on naming — MLB uses home_ml_open / away_ml_open while
--     NFL and NCAAF use open_home_ml / open_away_ml — and plpgsql resolves
--     OLD.<field> at RUNTIME, so the function installed cleanly and only
--     exploded on a real write. Verified today: open_home_ml exists ONLY on
--     nfl_game_context and ncaaf_game_context, while the trigger is attached to
--     all six. Re-introducing a hardcoded column list would have re-broken
--     MLB, NHL, NBA and NCAAB context writes.
--
--     So the freeze body below is 20260919b's jsonb walk, UNCHANGED, with only
--     the marker check added ahead of it. The RPC is likewise driven by a jsonb
--     payload and checks each column against the live table rather than
--     assuming a naming convention.
--
-- ══ WHY NOT JUST LET NULL THROUGH ══
-- Allowing a NULL to pass would give a two-step repair with no new objects.
-- Rejected: PostgREST batch upserts UNION their keys across rows, so a batch
-- where only some rows carry an open_* column sends NULL for the rest
-- (feedback_postgrest_batch_normalize_keys), turning an accidental key union
-- into a silent mass erase of real openers — worse than the bug being fixed.
--
-- ══ THE APPROACH: A TRANSACTIONAL MARKER ROW ══
-- The bypass is a row in `open_line_override` keyed by (table_name, game_id).
-- The trigger passes NEW through only when a marker exists for exactly the row
-- being written. repair_opening_line() inserts the marker, runs the UPDATE and
-- deletes the marker in one function body — therefore one transaction — so the
-- marker cannot outlive the call even if the UPDATE raises.
--
-- This is STRICTER than the GUC draft, not a concession: a session GUC would
-- have bypassed the freeze for EVERY row touched while set; a marker authorises
-- exactly one (table_name, game_id). It needs no special privileges, and a
-- marker left behind is a visible audit trail.
--
-- COST: one primary-key probe per context UPDATE, against a table that is empty
-- except during a repair. The context tables are patched in bulk by every
-- pipeline run, so this was weighed.
--
-- ROLLBACK: restore freeze_opening_lines() from 20260919b (NOT 20260917h), then
--   DROP FUNCTION public.repair_opening_line(text, text, jsonb);
--   DROP TABLE public.open_line_override;
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.open_line_override (
    table_name   text        NOT NULL,
    game_id      text        NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (table_name, game_id)
);

COMMENT ON TABLE public.open_line_override IS
    'Single-row bypass tokens for freeze_opening_lines. A row authorises ONE '
    '(table_name, game_id) to have its open-line columns rewritten. Written and '
    'removed inside repair_opening_line() in the same transaction, so a row '
    'persisting here means a repair was attempted by hand — treat that as an '
    'audit finding, not normal state.';

ALTER TABLE public.open_line_override ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.open_line_override FROM PUBLIC;


-- Body is 20260919b's jsonb walk verbatim; only the marker check is new.
-- SECURITY DEFINER is load-bearing, not boilerplate: open_line_override has
-- RLS enabled and no policies, so a trigger running as the INVOKING role would
-- read zero rows from it and the bypass would silently never fire — the repair
-- would report success and change nothing, which is the same class of failure
-- this file exists to fix. search_path is pinned because a SECURITY DEFINER
-- function must not resolve object names through the caller's path.
CREATE OR REPLACE FUNCTION public.freeze_opening_lines() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $fn$
DECLARE
    _old  jsonb := to_jsonb(OLD);
    _new  jsonb := to_jsonb(NEW);
    _col  text;
    _hit  boolean := false;
    -- Both naming conventions. A table only has some of these; the
    -- `_old ? _col` existence check skips the rest safely.
    _cols text[] := ARRAY[
        'open_home_ml', 'open_away_ml',     -- NFL / NCAAF
        'home_ml_open', 'away_ml_open',     -- MLB
        'open_spread',  'open_total',       -- shared
        'open_puckline'                     -- NHL
    ];
BEGIN
    -- The one escape hatch, scoped to a single row. repair_opening_line()
    -- holds the marker only for the duration of its own transaction.
    IF EXISTS (SELECT 1 FROM public.open_line_override o
                WHERE o.table_name = TG_TABLE_NAME
                  AND o.game_id    = NEW.game_id) THEN
        RETURN NEW;
    END IF;

    FOREACH _col IN ARRAY _cols LOOP
        -- Only touch columns this table actually has, that already hold a
        -- value. First write wins; later refreshes cannot overwrite it.
        IF (_old ? _col) AND (_old -> _col) IS NOT NULL
           AND jsonb_typeof(_old -> _col) <> 'null' THEN
            _new := jsonb_set(_new, ARRAY[_col], _old -> _col, true);
            _hit := true;
        END IF;
    END LOOP;

    IF NOT _hit THEN
        RETURN NEW;          -- nothing to freeze, avoid a needless rebuild
    END IF;
    RETURN jsonb_populate_record(NEW, _new);
END;
$fn$;

COMMENT ON FUNCTION public.freeze_opening_lines() IS
    'Preserves already-populated open-line columns against the odds pullers, '
    'which rewrite them on every upsert. Schema-agnostic via to_jsonb(OLD) so '
    'it is correct for every sport (see 20260919b). Bypassed only for a '
    '(table_name, game_id) holding a marker row in open_line_override, which '
    'repair_opening_line() keeps for the duration of one transaction.';


-- jsonb payload rather than fixed parameters, for the same reason the trigger
-- walks jsonb: the open-line columns differ per sport, and a signature naming
-- open_home_ml cannot repair an MLB row whose column is home_ml_open.
CREATE OR REPLACE FUNCTION public.repair_opening_line(
    p_table   text,
    p_game_id text,
    p_values  jsonb
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $fn$
DECLARE
    v_allowed_tables text[] := ARRAY[
        'mlb_game_context', 'nfl_game_context', 'ncaaf_game_context',
        'nhl_game_context', 'nba_game_context', 'ncaab_game_context'];
    v_allowed_cols text[] := ARRAY[
        'open_home_ml', 'open_away_ml', 'home_ml_open', 'away_ml_open',
        'open_spread', 'open_total', 'open_puckline'];
    v_col  text;
    v_sets text[] := ARRAY[]::text[];
    v_n    integer;
BEGIN
    -- Whitelist, not interpolation. p_table reaches a format() either way, so
    -- it must be proven to be one of ours before it gets there.
    IF NOT (p_table = ANY(v_allowed_tables)) THEN
        RAISE EXCEPTION 'repair_opening_line: % is not a context table', p_table;
    END IF;
    IF p_game_id IS NULL OR length(p_game_id) = 0 THEN
        RAISE EXCEPTION 'repair_opening_line: game_id is required';
    END IF;
    IF p_values IS NULL OR p_values = '{}'::jsonb THEN
        RAISE EXCEPTION 'repair_opening_line: no columns supplied';
    END IF;

    FOR v_col IN SELECT jsonb_object_keys(p_values) LOOP
        IF NOT (v_col = ANY(v_allowed_cols)) THEN
            RAISE EXCEPTION 'repair_opening_line: % is not an open-line column',
                            v_col;
        END IF;
        -- And it must exist on THIS table. Refusing is better than a 42703
        -- surfacing to the caller as an opaque 400.
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name   = p_table
                          AND column_name  = v_col) THEN
            RAISE EXCEPTION 'repair_opening_line: %.% does not exist',
                            p_table, v_col;
        END IF;
        v_sets := v_sets || format('%I = ($1 ->> %L)::numeric', v_col, v_col);
    END LOOP;

    INSERT INTO public.open_line_override (table_name, game_id)
    VALUES (p_table, p_game_id)
    ON CONFLICT (table_name, game_id) DO UPDATE SET requested_at = now();

    EXECUTE format('UPDATE public.%I SET %s WHERE game_id = $2',
                   p_table, array_to_string(v_sets, ', '))
    USING p_values, p_game_id;

    GET DIAGNOSTICS v_n = ROW_COUNT;

    -- Same transaction as the INSERT above, so an exception anywhere in this
    -- body rolls the marker back rather than leaving the freeze disarmed.
    DELETE FROM public.open_line_override
     WHERE table_name = p_table AND game_id = p_game_id;

    RETURN v_n;
END;
$fn$;

COMMENT ON FUNCTION public.repair_opening_line(text, text, jsonb) IS
    'The only sanctioned way to change a stored opening line. One table, one '
    'game_id, and a jsonb of open-line columns checked against both an '
    'allow-list and the live table. Returns rows affected — 0 means the '
    'game_id did not match, which a caller must treat as a failure.';

REVOKE ALL ON FUNCTION public.repair_opening_line(text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.repair_opening_line(text, text, jsonb) TO service_role;

NOTIFY pgrst, 'reload schema';
