-- Give every published pick a price, so ROI and CLV are computable.
--
-- 2026-09-23. Andy: "Why no price we should price and pick for each pick
-- to track roi." A pick with a line and no price has no ROI — you cannot
-- say what it returned, only whether it won, and those are different
-- questions once juice varies from -102 to -125.
--
-- The price was never missing from the system. line_history holds 4,584
-- NFL rows for this week alone across 11 books, each with market, side,
-- line and price. What was missing was anywhere to put it on the pick:
-- jerry_reads has no price column at all, and nfl_game_picks.odds_american
-- is populated for moneylines only.
--
-- Columns are deliberately sport-neutral and added to both the game-read
-- surface and the picks table, because the two disagree today about which
-- market a game is being played on and both get consumed.

ALTER TABLE IF EXISTS public.jerry_reads
  ADD COLUMN IF NOT EXISTS price_american   integer,
  ADD COLUMN IF NOT EXISTS price_best       integer,
  ADD COLUMN IF NOT EXISTS price_best_book  text,
  ADD COLUMN IF NOT EXISTS price_books_n    integer,
  ADD COLUMN IF NOT EXISTS price_implied    numeric(6,4),
  ADD COLUMN IF NOT EXISTS priced_at        timestamptz,
  -- captured at kickoff, not at pick time: this is the CLV half. B32
  -- records close_over_odds present on 68 of 26,040 MLB props and 0 of
  -- 1,317 NFL, so closing price has effectively never been stored.
  ADD COLUMN IF NOT EXISTS close_price_american integer,
  ADD COLUMN IF NOT EXISTS close_priced_at      timestamptz;

COMMENT ON COLUMN public.jerry_reads.price_american IS
  'Consensus American price for THIS pick at THIS line, median taken in '
  'probability space across books quoting the same line. Never a price '
  'from a different line — that is a different bet.';
COMMENT ON COLUMN public.jerry_reads.price_books_n IS
  'How many books backed the consensus. A price from one book is not a '
  'consensus; surfaces should gate on this rather than trust the number.';
COMMENT ON COLUMN public.jerry_reads.close_price_american IS
  'Price at kickoff. price_american minus this is closing-line value, '
  'the only honest short-run read on whether selection is working.';

-- nfl_game_picks already has odds_american but only moneylines ever fill
-- it. Give it the same shape so spread and total picks stop being blank.
ALTER TABLE IF EXISTS public.nfl_game_picks
  ADD COLUMN IF NOT EXISTS price_best       integer,
  ADD COLUMN IF NOT EXISTS price_best_book  text,
  ADD COLUMN IF NOT EXISTS price_books_n    integer,
  ADD COLUMN IF NOT EXISTS price_implied    numeric(6,4),
  ADD COLUMN IF NOT EXISTS priced_at        timestamptz,
  ADD COLUMN IF NOT EXISTS close_price_american integer,
  ADD COLUMN IF NOT EXISTS close_priced_at      timestamptz;

-- Finding a pick's quotes means hitting line_history by its rebuilt key.
-- Without this the pricer scans the whole table per window.
CREATE INDEX IF NOT EXISTS line_history_sport_commence_idx
  ON public.line_history (sport, commence_time DESC);
CREATE INDEX IF NOT EXISTS line_history_game_market_idx
  ON public.line_history (game_id, market, side);

-- Unpriced published picks should be visible without anyone remembering
-- to look, because a blank price is exactly the kind of gap that sat
-- unnoticed for a whole season.
CREATE OR REPLACE VIEW public.v_unpriced_published_picks AS
SELECT sport, game_date, game_id, call_market, call_side, call_line,
       call_text, conviction, generated_at
  FROM public.jerry_reads
 WHERE price_american IS NULL
   AND call_text IS NOT NULL
   AND game_date >= (now() AT TIME ZONE 'America/New_York')::date - 14;

NOTIFY pgrst, 'reload schema';
