-- 2026-09-19b — CRITICAL: freeze_opening_lines() blocks every MLB
--                game_context UPDATE.
-- ==================================================================
-- SYMPTOM (MLB Pipeline Daily #1097, and every run since 2026-09-17):
--   Upload failed 400: {"code":"42703",
--     "message":"record \"old\" has no field \"open_home_ml\""}
--   ❌ Failed: Milwaukee Brewers @ Baltimore Orioles
--   ❌ Failed: Philadelphia Phillies @ New York Mets
--   ❌ Failed: Athletics @ Cleveland Guardians
--   ...repeating for most of the slate, every run.
--
-- CAUSE: 20260917h installed freeze_opening_lines() on every *_game_context
-- table. The function hardcodes OLD.open_home_ml / OLD.open_away_ml, and
-- the DO block guarded on the TABLE existing — never on the COLUMNS. But
-- the sports disagree on naming:
--
--     mlb_game_context   -> home_ml_open / away_ml_open   (NO open_*_ml)
--     nfl_game_context   -> open_home_ml / open_away_ml
--     ncaaf_game_context -> open_home_ml / open_away_ml
--
-- Postgres resolves OLD.<field> at RUNTIME for plpgsql, so the function
-- created fine and only exploded when a row was actually UPDATEd. 42703 is
-- undefined_column raised from inside the trigger, which PostgREST returns
-- as a 400 on the caller's write.
--
-- BLAST RADIUS: every UPDATE to mlb_game_context has failed since 9/17.
-- INSERTs of brand-new rows still worked (no OLD record), so the table
-- looked alive while silently refusing all refreshes — stale primary_play,
-- close lines never updating, scores never landing, mlb_game_results
-- holding 5 rows for a 15-game slate. A large share of the grading and
-- card-composition problems chased on 9/18-9/19 trace back here.
--
-- FIX: make the function schema-agnostic. Walk the open-line columns via
-- to_jsonb(OLD) and only freeze the ones that actually exist on the row
-- being updated. One function, correct for every sport, and it cannot
-- break again when a new sport spells its columns differently.
--
-- Covers BOTH naming conventions so MLB is genuinely protected rather
-- than merely un-broken.
--
-- ROLLBACK (removes opening-line freeze entirely):
--   DROP TRIGGER IF EXISTS trg_freeze_opening_lines_mlb ON public.mlb_game_context;
--   ...repeat per sport... then DROP FUNCTION public.freeze_opening_lines();
-- ==================================================================

CREATE OR REPLACE FUNCTION public.freeze_opening_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    _old  jsonb := to_jsonb(OLD);
    _new  jsonb := to_jsonb(NEW);
    _col  text;
    _hit  boolean := false;
    -- Both naming conventions. A table only has some of these; the
    -- `_old ? _col` existence check skips the rest safely.
    _cols text[] := ARRAY[
        'open_home_ml', 'open_away_ml',     -- NFL / NCAAF / NBA / NHL / NCAAB
        'home_ml_open', 'away_ml_open',     -- MLB
        'open_spread',  'open_total'        -- shared
    ];
BEGIN
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
$$;

-- Triggers themselves are unchanged (still BEFORE UPDATE FOR EACH ROW on
-- each *_game_context). Replacing the function body is enough — but
-- re-assert MLB's in case a prior failed run left it absent.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'mlb_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_freeze_opening_lines_mlb ON public.mlb_game_context';
        EXECUTE 'CREATE TRIGGER trg_freeze_opening_lines_mlb
                 BEFORE UPDATE ON public.mlb_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.freeze_opening_lines()';
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
