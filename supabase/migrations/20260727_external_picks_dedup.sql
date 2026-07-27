-- ============================================================
-- Dedupe external_picks: same source × game × surface × side should
-- exist ONCE per game_date, not one row per cron pull.
-- ============================================================
-- Bug: pull_externals_mlb.py runs 3 crons/day and INSERTs on every
-- pull. Same source posting the same pick across all 3 pulls produced
-- 3 rows in external_picks. Consensus counts ("8/10 books on HOME")
-- inflated by ~2-3x, which mis-triggered fade detector alignment math.
--
-- Verified 2026-07-27: action.ml on ARI/PIT posted 3 rows (13:12,
-- 17:46, 20:05) all with pick_side=HOME. 43 exact-dupe keys total
-- across today's 107 rows.
-- ============================================================
-- Fix in two steps:
--   1. This migration adds a partial unique index enforcing
--      one row per (source, game_id, surface, pick_side, game_date).
--      pull_id + pulled_at can still differ (upserts refresh timestamps).
--   2. pull_externals_mlb.py write_picks() must be updated to
--      POST with Prefer: resolution=merge-duplicates,on_conflict=...
--
-- Idempotent: DROP + CREATE. Safe to re-run.
-- ============================================================

-- Step A: clean existing dupes first — keep most recent per key
WITH ranked AS (
  SELECT
    id,
    row_number() OVER (
      PARTITION BY source, game_id, surface, pick_side, game_date
      ORDER BY pulled_at DESC
    ) AS rn
  FROM external_picks
  WHERE game_id IS NOT NULL
)
DELETE FROM external_picks
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- Step B: enforce uniqueness going forward
DROP INDEX IF EXISTS idx_external_picks_dedup;
CREATE UNIQUE INDEX idx_external_picks_dedup
  ON external_picks (source, game_id, surface, pick_side, game_date)
  WHERE game_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
