-- 20261028a · DAWG OF THE DAY CAN ONLY HOLD ONE PICK A DAY, AND HAS NO PRICE
--
-- Andy, 2026-10-28: "Wiring all sports into Dawg of the Day and ensuring that
-- if its plus odds it waulifies as Dawg so if engine like a -1.5 Puckline or
-- other spread thats pluss odds that should qualify as dawg as well right?"
--
-- Three things stop that today, and all three are schema:
--
-- 1. `game_date` is the PRIMARY KEY. One row per calendar day, for every
--    sport combined. A multi-sport Dawg physically cannot be stored.
--
-- 2. There is no `sport` column at all. Every one of the 132 rows is MLB
--    because generate_dawg_of_day.py is hardcoded to `baseball_mlb`, and
--    nothing downstream could tell them apart if it were not.
--
-- 3. There is no PRICE column. Dawg is a PLUS-ODDS surface by definition —
--    its own gate is MIN_ML_PRICE 100 / MAX_ML_PRICE 250 — and its record,
--    60-68-4 all time, has never been expressible in units. 0 of 132 dawg
--    receipts carry pick_odds. A 46.9% hit rate is profitable at +150 and
--    losing at +110, and we cannot currently tell which this was. Same hole
--    found on POTD today (69% of its real picks have no price).
--
-- `market` is added for the second half of Andy's instruction. A plus-odds
-- SPREAD is a different population from a plus-odds MONEYLINE and must be
-- recorded as such rather than blended — measured on every plus-odds pick we
-- have ever published:
--
--     market       n     W    L    hit%     units    breakeven
--     ML          80    42   38   52.5%   +12.44u      46.7%
--     SPREAD/PL   21     6   15   28.6%    -7.94u      46.5%
--
-- so a blended "Dawg record" would hide which half works
-- (feedback_ml_vs_rl_conflation). The composer prefers ML and may take a
-- plus-odds spread; the record splits by this column.
--
-- READ-NEUTRAL FOR EXISTING ROWS. The 132 MLB rows are backfilled to
-- sport='MLB', market='ml' (every one of them was a moneyline) and keep
-- price_american NULL, which is honest — that price was never captured and
-- inventing one now would fabricate a unit figure.

BEGIN;

ALTER TABLE public.daily_dawg
    ADD COLUMN IF NOT EXISTS sport           TEXT,
    ADD COLUMN IF NOT EXISTS market          TEXT,
    ADD COLUMN IF NOT EXISTS price_american  INTEGER,
    ADD COLUMN IF NOT EXISTS price_implied   NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS priced_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pick_line       NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS pick_side       TEXT;

-- Existing rows are all MLB moneylines. Stated, not guessed: the generator
-- has only ever queried baseball_mlb and only ever gated on MIN/MAX_ML_PRICE.
UPDATE public.daily_dawg SET sport = 'MLB' WHERE sport IS NULL;
UPDATE public.daily_dawg SET market = 'ml' WHERE market IS NULL;

-- One Dawg per (sport, date) instead of one per date. Done as a UNIQUE INDEX
-- rather than a new primary key so the existing PK and every FK that may
-- reference it keep working; the writer upserts on this.
CREATE UNIQUE INDEX IF NOT EXISTS daily_dawg_sport_date_uniq
    ON public.daily_dawg (sport, game_date);

COMMENT ON COLUMN public.daily_dawg.market IS
  'ml or rl. A plus-odds SPREAD is a different population from a plus-odds '
  'MONEYLINE (ML 52.5% +12.44u on n=80 vs SPREAD 28.6% -7.94u on n=21), so '
  'the Dawg record must split by this rather than blend them.';
COMMENT ON COLUMN public.daily_dawg.price_american IS
  'The published price. Dawg is a plus-odds surface by definition and its '
  '60-68-4 record was never expressible in units without this.';
COMMENT ON INDEX public.daily_dawg_sport_date_uniq IS
  'One Dawg per sport per day. game_date alone was the PK, so a multi-sport '
  'Dawg could not be stored at all. See 20261028a.';

-- Force PostgREST schema cache reload — without this, PGRST204 on writes to
-- the new columns for up to 10 minutes.
NOTIFY pgrst, 'reload schema';

COMMIT;
