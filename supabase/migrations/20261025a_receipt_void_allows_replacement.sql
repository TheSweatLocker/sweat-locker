-- 20261025a · A VOIDED RECEIPT STILL OCCUPIES ITS UNIQUE KEY, SO A
--              CORRECT-SIDE REPLACEMENT CANNOT BE WRITTEN
--
-- Andy, 2026-10-25, on five NFL game_read receipts frozen on a side we did
-- not publish: "Void and publish correct". The void half worked. Every
-- replacement INSERT was then refused:
--
--   23505  Key (sport, surface, game_date, source_id)
--          = (NFL, game_read, 2026-10-25, 8939) already exists
--          violates "public_receipts_sport_surface_game_date_source_id_key"
--
-- The constraint permits exactly ONE receipt per (sport, surface, date,
-- source_id) regardless of result, and a game_read receipt's source_id is its
-- `jerry_reads.id`. So the voided row keeps the only slot its own game has.
-- There is no legitimate way around it in application code:
--   * `pick_side` / `pick_line` are immutable by trigger, so the voided row
--     cannot be corrected in place (B181's one-way door);
--   * `nfl_game_context` has no integer id, so the replacement cannot honestly
--     be sourced from the badge surface instead -- verified, and all 7 NFL
--     game_read receipts source from jerry_reads;
--   * DELETEing the voided row would destroy the audit trail of the void,
--     which is the one thing a receipts table exists to keep.
--
-- Net effect before this migration: the only available remedy for a wrong-side
-- receipt is to void it and lose the play from the denominator entirely. Five
-- real plays on 10-25 are in exactly that state.
--
-- WHAT THIS CHANGES. The uniqueness rule becomes "one LIVE receipt per source
-- per day", which is what the constraint was actually for -- stopping the
-- writers from double-publishing the same read. Any number of VOIDED rows may
-- sit alongside it, because a void is history and history should accumulate.
--
-- Deliberately NOT a plain DROP: the partial index keeps the duplicate-write
-- protection fully intact for every row that counts toward a record.
--
-- `lower(result)` rather than `result`: the result column is Title case in
-- places ('Win'/'Loss') and lowercase in others ('void' as written by the
-- voider), so comparing the raw value would leave a 'Void' row still holding
-- the key. lower() is immutable and legal in an index predicate.
--
-- READ-NEUTRAL. No column is added, dropped or retyped, and no row is
-- modified. Existing SELECTs are unaffected.

BEGIN;

ALTER TABLE public.public_receipts
  DROP CONSTRAINT IF EXISTS public_receipts_sport_surface_game_date_source_id_key;

-- One live receipt per (sport, surface, game_date, source_id).
-- Voided rows are exempt and may accumulate.
CREATE UNIQUE INDEX IF NOT EXISTS public_receipts_live_source_uniq
    ON public.public_receipts (sport, surface, game_date, source_id)
 WHERE result IS NULL OR lower(result) <> 'void';

COMMENT ON INDEX public.public_receipts_live_source_uniq IS
  'One LIVE receipt per (sport, surface, game_date, source_id). Replaces '
  'public_receipts_sport_surface_game_date_source_id_key, which counted '
  'voided rows and so made a correct-side replacement impossible after a '
  'void. See 20261025a.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- VERIFY (expect: the old constraint gone, the partial index present, and
-- zero duplicate LIVE receipts -- the last must return 0 rows):
--
--   SELECT conname FROM pg_constraint
--    WHERE conrelid = 'public.public_receipts'::regclass
--      AND conname = 'public_receipts_sport_surface_game_date_source_id_key';
--
--   SELECT indexname FROM pg_indexes
--    WHERE tablename = 'public_receipts'
--      AND indexname = 'public_receipts_live_source_uniq';
--
--   SELECT sport, surface, game_date, source_id, count(*)
--     FROM public.public_receipts
--    WHERE result IS NULL OR lower(result) <> 'void'
--    GROUP BY 1,2,3,4 HAVING count(*) > 1;
