-- primary_play_snapshots — switch from overwrite-per-source to append-per-publish
--
-- 2026-08-23 background: original 20260821 migration had unique index on
-- (game_id, snapshot_source) so every recompute UPDATED the same row per
-- source. That collapsed the audit trail we wanted to build in the first
-- place — we only get the LATEST recompute + the LATEST card_lock per
-- game, not every intermediate publish across the day.
--
-- User picked "freeze on every publish (verbose, most accurate)" for the
-- snapshot table scope. This migration:
--   1. Drops the (game_id, snapshot_source) unique constraint
--   2. Adds a plain (game_id, snapshot_source, snapshotted_at) index
--      for fast per-game timelines
--   3. Keeps game_date/sport index for slate-wide audits
--
-- After this, each POST to primary_play_snapshots appends a new row —
-- one per publish event across a game's day. ~30 rows/game/day for MLB.

DROP INDEX IF EXISTS public.primary_play_snapshots_uniq;

CREATE INDEX IF NOT EXISTS primary_play_snapshots_game_source_time_idx
  ON public.primary_play_snapshots (game_id, snapshot_source, snapshotted_at DESC);

COMMENT ON TABLE public.primary_play_snapshots IS
  'Append-only per-publish snapshots of game ensemble picks. Every game_context write + recompute writes a row. Feeds signal-level audits + drift detection.';

NOTIFY pgrst, 'reload schema';
