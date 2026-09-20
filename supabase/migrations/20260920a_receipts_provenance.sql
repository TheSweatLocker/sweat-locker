-- 2026-09-20  public_receipts: provenance + joinability
--
-- CONTEXT
-- public_receipts is the only place a published pick is preserved with
-- its tier, conviction, odds, line and result at publish time. It is what
-- answers "prove that record" when someone challenges a posted number.
-- Today it holds 9,872 MLB rows (2026-07-30 .. 2026-09-18) and produces
-- a PRIME prop record of 597-123 (82.9%) that reconciles with an
-- independent calculation — the first number in this system that two
-- separate methods agree on.
--
-- It has two gaps this migration closes.
--
-- 1. capture_mode — PROVENANCE.
--    Every existing row was written by backfill_public_receipts.py after
--    the fact, not captured at publish time. A receipt assembled later is
--    weaker evidence than one demonstrably written before the game: the
--    former can only be as honest as the table it was rebuilt from, and
--    those tables mutate (tier churn, line drift, row replacement — all
--    observed on 2026-09-19/20).
--
--    Mixing the two silently would be the exact integrity problem the
--    receipts table exists to solve. So every row is labelled.
--      'live'          — written at publish time by the publisher
--      'reconstructed' — rebuilt afterwards from a source table
--    Existing rows are backfilled to 'reconstructed' because that is what
--    they are. New live writes must pass 'live' explicitly.
--
-- 2. game_id — JOINABILITY.
--    Receipts carry source_table/source_id but no game_id, so a receipt
--    cannot be joined to its game context, result, or the other picks on
--    the same game. That makes per-game audit ("show me everything we
--    said about this matchup") impossible without a lookup chain.
--
-- Nothing is deleted or overwritten. Both columns are additive and
-- nullable; existing readers are unaffected.

ALTER TABLE public.public_receipts
  ADD COLUMN IF NOT EXISTS capture_mode text,
  ADD COLUMN IF NOT EXISTS game_id      text;

-- Label the existing 9,872 rows for what they are. Deliberately explicit
-- rather than a DEFAULT: a default would quietly stamp future rows too,
-- and 'live' must be an affirmative claim by the writer, never an
-- assumption.
UPDATE public.public_receipts
   SET capture_mode = 'reconstructed'
 WHERE capture_mode IS NULL;

ALTER TABLE public.public_receipts
  ADD CONSTRAINT public_receipts_capture_mode_chk
  CHECK (capture_mode IN ('live', 'reconstructed'));

CREATE INDEX IF NOT EXISTS public_receipts_game_id_idx
    ON public.public_receipts (sport, game_id);
CREATE INDEX IF NOT EXISTS public_receipts_capture_mode_idx
    ON public.public_receipts (capture_mode, sport, game_date);

COMMENT ON COLUMN public.public_receipts.capture_mode IS
  'live = written by the publisher at publish time (strong evidence). '
  'reconstructed = rebuilt after the fact by backfill_public_receipts.py '
  'from a mutable source table (weaker). Never present one as the other; '
  'any public record claim should state which it is built from.';
COMMENT ON COLUMN public.public_receipts.game_id IS
  'Joins the receipt to its <sport>_game_context / _game_results row so a '
  'single game can be audited end to end.';

NOTIFY pgrst, 'reload schema';
