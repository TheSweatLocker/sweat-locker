-- 20261028b · daily_dawg.spread_delta AND .tier ARE MLB-GENERATOR CONCEPTS
--
-- Follow-up to 20261028a, found by running the new writer against it. Both
-- inserts failed:
--
--   23502  Failing row contains (2026-10-28, Tampa Bay Rays ML,
--          Milwaukee Brewers @ Tampa Bay Rays, 99d7f299…, null, null, …
--
-- `spread_delta` and `tier` are NOT NULL. Both come from the original
-- MLB-only selector, which identified a dog by the gap between our projected
-- spread and the market's (`spread_delta`) and then scored it into its own
-- PRIME/STRONG/LEAN ladder (`tier`).
--
-- generate_dawg_multisport.py selects differently: it takes the engine's own
-- published read when that read is priced in the plus-odds band. There is no
-- projected-spread gap behind an NHL moneyline chosen that way, and no Dawg
-- tier either.
--
-- WHY NOT JUST WRITE A NUMBER. spread_delta 0.0 would be a fabricated
-- measurement in a column whose whole meaning is "how far our number is from
-- the market's" — and it is surfaced, so the card would read "model edge
-- 0.0" on a play chosen for its price. Deriving `tier` from conviction is the
-- same error in the other direction: conviction does not mean the same thing
-- across sports. Measured 2026-10-28 on published reads, conviction 70+:
--
--     MLB    n=277  70.0%  +85.65u        <- it works, on baseball
--     NCAAF  n=187  50.3%  -14.75u        <- flat-to-inverted, both halves
--     NFL    n= 15  46.7%   -2.40u
--
-- so a PRIME label minted from an NHL conviction would be a baseball scale
-- wearing another sport's jersey. A NULL that says "this selector has no
-- tier" is the honest value.
--
-- NOTHING IS DROPPED OR RETYPED, and no existing row changes: all 132 rows
-- already hold both values from the MLB generator, which keeps writing them.

BEGIN;

ALTER TABLE public.daily_dawg
    ALTER COLUMN spread_delta DROP NOT NULL,
    ALTER COLUMN tier         DROP NOT NULL;

COMMENT ON COLUMN public.daily_dawg.spread_delta IS
  'Projected-vs-market spread gap. Set by the MLB generator only. NULL from '
  'generate_dawg_multisport, which selects on PRICE and has no such gap - a '
  '0.0 there would be a fabricated edge, not a missing one. See 20261028b.';
COMMENT ON COLUMN public.daily_dawg.tier IS
  'The MLB generator''s own PRIME/STRONG/LEAN ladder. NULL from the '
  'multi-sport writer unless the read carried a snapshot tier: conviction is '
  'a per-sport scale (MLB 70+ is 70.0% and +85.65u, NCAAF 70+ is 50.3% and '
  '-14.75u), so a tier minted from it would not mean what it says.';

NOTIFY pgrst, 'reload schema';

COMMIT;
