-- == 2026-10-22 - NBA IS THE ONLY SPORT WITH NO OPENING MONEYLINE COLUMN ==
--
-- ANDY: "Also in line voement section in game betail for nba opening lines i
-- dont thinkare showing they should show wiht an arrow to currne tline, line
-- moevemnt is improtant for nba so this should be showing".
--
-- Checked the three rows of that strip separately, because two of them are
-- fine and only one is broken.
--
-- SPREAD AND TOTAL ARE WORKING. preserve_opens() was added to
-- nba_game_context on 10-20 and it records an opener correctly - first
-- sighting wins, never overwritten. On the rows that have odds, the openers
-- are there AND they are moving:
--
--     of 14 NBA games with an opener on file
--       spread moved off its open on  3   (DAL -10.5 -> -9.5, UTA -2.5 ->
--                                          -1.5, CLE -2.5 -> -3.5)
--       total  moved off its open on  6
--
-- Games two and three days out show nothing because they have NO ODDS YET -
-- close_spread is NULL on 10/23 and 10/24, and an opener cannot be recorded
-- from a line that has not been posted. That is the mechanism working, and it
-- fills in by itself as the odds arrive.
--
-- ── THE MONEYLINE ROW, HOWEVER, CAN NEVER RENDER ────────────────────────
-- The client reads it as:
--
--     openHomeML = historicalOdds?.opening_ml_home ?? ctx?.home_ml_open
--
-- and `home_ml_open` DOES NOT EXIST on nba_game_context. Probed column by
-- column rather than inferred from one sport's schema, because this project
-- has two live naming conventions for the same field and they are not
-- interchangeable:
--
--     mlb_game_context     home_ml_open / away_ml_open        ✓
--     nhl_game_context     home_ml_open / away_ml_open        ✓
--     ncaab_game_context   home_ml_open / away_ml_open        ✓
--     nfl_game_context     BOTH conventions                   ✓
--     nba_game_context     NEITHER                            <- only gap
--
-- So NBA is the single sport missing it, and the ML row has shown
-- "— no open" on every NBA card since the sport went live.
--
-- ── WHY home_ml_open AND NOT open_home_ml ───────────────────────────────
-- Both names exist in this schema and either would work for storage. This
-- one is chosen because THE SHIPPED CLIENT ALREADY READS IT. 1.0.2 and 1.0.3
-- both fall back to `ctx?.home_ml_open`, so populating this exact name makes
-- the opening moneyline appear with NO app build and no App Store review -
-- which matters, because there is no OTA path (B5).
--
-- ── THE FREEZE TRIGGER NEEDS NO CHANGE, AND I CHECKED ───────────────────
-- `freeze_opening_lines()` (20260919b) iterates a hardcoded array that
-- already carries BOTH conventions - 'open_home_ml', 'open_away_ml',
-- 'home_ml_open', 'away_ml_open', 'open_spread', 'open_total' - and guards
-- each with `_old ? _col`, so a table that lacks a column is skipped rather
-- than erroring. Adding these two columns puts them under the existing
-- BEFORE UPDATE trigger automatically: the first non-null write wins and no
-- later refresh can overwrite it.
--
-- That trigger is also why the writer must only mirror into an EMPTY slot. A
-- bad first write is permanent here - the trigger silently discards the
-- correction and still returns 200 (the "a 204 is not a write" trap), so
-- there is no repairing it from the pipeline afterwards.

ALTER TABLE public.nba_game_context
  ADD COLUMN IF NOT EXISTS home_ml_open INTEGER,
  ADD COLUMN IF NOT EXISTS away_ml_open INTEGER;

COMMENT ON COLUMN public.nba_game_context.home_ml_open IS
  'Opening home moneyline, American. Written by nba_game_context.'
  'preserve_opens() on FIRST sighting only and frozen thereafter by '
  'trg_freeze_opening_lines_nba. Named to match mlb/nhl/ncaab and, more '
  'importantly, to match what the shipped client already reads '
  '(ctx.home_ml_open), so no app build is needed.';
COMMENT ON COLUMN public.nba_game_context.away_ml_open IS
  'Opening away moneyline, American. See home_ml_open.';

DO $$
DECLARE n INT; trg INT;
BEGIN
  SELECT COUNT(*) INTO n
    FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'nba_game_context'
     AND column_name IN ('home_ml_open', 'away_ml_open');
  IF n <> 2 THEN
    RAISE EXCEPTION 'expected both opening-ML columns on nba_game_context, '
                    'found %', n;
  END IF;

  -- The columns are worthless without the freeze, so assert it is there
  -- rather than assuming the 09-19 migration covered this table.
  SELECT COUNT(*) INTO trg
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
   WHERE c.relname = 'nba_game_context'
     AND NOT t.tgisinternal
     AND t.tgname LIKE '%freeze_opening_lines%';
  IF trg = 0 THEN
    RAISE WARNING 'nba_game_context has NO freeze_opening_lines trigger - '
                  'an opener here can be overwritten by a later pull. '
                  'preserve_opens() still guards it in code, but the DB '
                  'guarantee is missing.';
  ELSE
    RAISE NOTICE 'opening-ML columns added; freeze trigger present (% on '
                 'this table)', trg;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
