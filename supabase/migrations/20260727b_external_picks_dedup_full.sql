-- ============================================================
-- Follow-up to 20260727_external_picks_dedup.sql.
-- ============================================================
-- The prior migration added a PARTIAL unique index
-- (WHERE game_id IS NOT NULL). Postgres won't accept it as a target
-- for ON CONFLICT without a WHERE hint, and PostgREST's on_conflict
-- query param doesn't support the hint. Result: upsert path 42P10
-- errors — writers fall back to per-row insert-with-skip.
--
-- Fix: add a NON-PARTIAL unique constraint on the same columns.
-- Rows with NULL game_id (rare — nothing writes them today) will
-- collide as (source, NULL, surface, side, date), which is fine
-- since Postgres treats NULLs as distinct in unique indexes.
-- ============================================================

-- First drop the partial index so we don't have two competing dedup
-- rules on the same columns.
DROP INDEX IF EXISTS idx_external_picks_dedup;

-- Add non-partial unique constraint. Idempotent via
-- ADD CONSTRAINT + IF NOT EXISTS on constraint name.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'external_picks_dedup_key'
  ) THEN
    ALTER TABLE external_picks
      ADD CONSTRAINT external_picks_dedup_key
      UNIQUE (source, game_id, surface, pick_side, game_date);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
