-- 2026-09-17h — opening-line freeze trigger.
-- ==================================================================
-- Andy 9/17 post-1.0.1 submission audit: Line Movement strip on game
-- detail was showing "no open" or open==close because the odds pull
-- scripts unconditionally rewrite open_* columns on every DB upsert.
--
-- Concrete case: Syracuse @ Pittsburgh NCAAF row this evening had
--   open_home_ml: -425  close_home_ml: -425
--   open_spread: -10.5  close_spread: -10.5
--   open_total:  51.5   close_total:  51.5
-- with close_locked_at = NULL — no true opening captured, just today's
-- pull mirrored into both columns.
--
-- Root cause (verified in ncaaf_odds_pull.py:207-217 and
-- nfl_odds_pull.py:191-202): scripts build a fresh row dict, set
-- open_* to the current pulled price, then upsert. UPSERT UPDATE
-- overwrites the DB's true opening line with today's close.
--
-- Fix: BEFORE UPDATE trigger on each *_game_context table. When the
-- existing DB row already has a non-NULL open_* value, preserve it —
-- reject the incoming write for that specific column. NULL open_*
-- rows (never seeded) still accept a first pull (the fallback also
-- writes close-as-open, but that's the initial snapshot).
--
-- Universal across sports; new sport ingestors inherit for free.
--
-- ROLLBACK: DROP TRIGGER per table + DROP FUNCTION.
-- ==================================================================

CREATE OR REPLACE FUNCTION public.freeze_opening_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- For each open_* column: if OLD has a value, keep it; else accept NEW.
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

-- Enroll every sport's game_context table that carries open_* columns.
-- IF EXISTS guards keep this safe for pre-launch sport tables.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'mlb_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_freeze_opening_lines_mlb ON public.mlb_game_context';
        EXECUTE 'CREATE TRIGGER trg_freeze_opening_lines_mlb
                 BEFORE UPDATE ON public.mlb_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.freeze_opening_lines()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'nfl_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_freeze_opening_lines_nfl ON public.nfl_game_context';
        EXECUTE 'CREATE TRIGGER trg_freeze_opening_lines_nfl
                 BEFORE UPDATE ON public.nfl_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.freeze_opening_lines()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'ncaaf_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_freeze_opening_lines_ncaaf ON public.ncaaf_game_context';
        EXECUTE 'CREATE TRIGGER trg_freeze_opening_lines_ncaaf
                 BEFORE UPDATE ON public.ncaaf_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.freeze_opening_lines()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'nba_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_freeze_opening_lines_nba ON public.nba_game_context';
        EXECUTE 'CREATE TRIGGER trg_freeze_opening_lines_nba
                 BEFORE UPDATE ON public.nba_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.freeze_opening_lines()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'nhl_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_freeze_opening_lines_nhl ON public.nhl_game_context';
        EXECUTE 'CREATE TRIGGER trg_freeze_opening_lines_nhl
                 BEFORE UPDATE ON public.nhl_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.freeze_opening_lines()';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'ncaab_game_context') THEN
        EXECUTE 'DROP TRIGGER IF EXISTS trg_freeze_opening_lines_ncaab ON public.ncaab_game_context';
        EXECUTE 'CREATE TRIGGER trg_freeze_opening_lines_ncaab
                 BEFORE UPDATE ON public.ncaab_game_context
                 FOR EACH ROW EXECUTE FUNCTION public.freeze_opening_lines()';
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
