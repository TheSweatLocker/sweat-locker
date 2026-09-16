-- 2026-09-16e · Add open_home_ml / open_away_ml columns to
-- nfl_game_context + ncaaf_game_context
--
-- Andy screenshot audit: NFL card Line Movement box shows "ML (HOME)
-- — → -218 · no open" on every game. Root cause: the columns
-- open_home_ml and open_away_ml DO NOT EXIST in the schema — we've
-- never captured opening ML. Only close_home_ml / close_away_ml
-- exist. Spread + total have both open + close.
--
-- This migration:
--   1) Adds open_home_ml, open_away_ml columns (nullable INT8)
--   2) Same for NCAAF (parity)
--
-- Backfill: any games with existing bookmaker line-history rows can
-- be filled from mlb_line_history / nfl_line_history table's earliest
-- ML snapshot per game. Handled by a follow-up script.
--
-- Ingest patch (separate): odds pull needs to snapshot the OPEN ML
-- on first fetch per game (Wed morning for NFL, Sun morning for
-- NCAAF) so the movement chart has an anchor.

BEGIN;

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS open_home_ml BIGINT,
  ADD COLUMN IF NOT EXISTS open_away_ml BIGINT;

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS open_home_ml BIGINT,
  ADD COLUMN IF NOT EXISTS open_away_ml BIGINT;

COMMENT ON COLUMN public.nfl_game_context.open_home_ml IS
  'Opening moneyline for home team, captured on first odds pull of the play-week (Wed AM). Powers Line Movement box "ML (HOME)" open→close display.';
COMMENT ON COLUMN public.nfl_game_context.open_away_ml IS
  'Opening moneyline for away team, captured on first odds pull of the play-week. Line Movement open→close display.';

COMMIT;

NOTIFY pgrst, 'reload schema';
