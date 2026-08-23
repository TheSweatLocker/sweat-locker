-- primary_play_snapshots — combined create + append-mode setup
--
-- 2026-08-23 background: 20260821 (create with UNIQUE index) and
-- 20260823 (drop unique, add append index) were both written but the
-- 08-21 CREATE never actually ran against Supabase, so the 08-23 drop
-- errored with 42P01 "relation does not exist" when the user tried it.
--
-- This is the CORRECT single-script setup — creates the table AND the
-- append-mode indexes in one round trip. Supersedes 20260821 and
-- 20260823 for fresh environments; a no-op on any environment that
-- happens to have run those already.
--
-- Both game_context.upload_game_context AND recompute_primary_play now
-- POST to this table on every publish (append, no on_conflict). Every
-- snapshot is one row — audit trail across all publishes for a game.

CREATE TABLE IF NOT EXISTS public.primary_play_snapshots (
  id              BIGSERIAL PRIMARY KEY,
  sport           TEXT NOT NULL,
  game_date       DATE NOT NULL,
  game_id         TEXT NOT NULL,
  snapshotted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  snapshot_source TEXT NOT NULL,
  home_team       TEXT,
  away_team       TEXT,
  primary_play    JSONB NOT NULL,
  pick_type       TEXT,
  pick_label      TEXT,
  pick_side       TEXT,
  pick_line       NUMERIC,
  tier            TEXT,
  conviction      INT,
  score           NUMERIC,
  result          TEXT,
  graded_at       TIMESTAMPTZ,
  actual_value    NUMERIC
);

-- Append-mode indexes (no unique constraint — every publish appends)
CREATE INDEX IF NOT EXISTS primary_play_snapshots_game_source_time_idx
  ON public.primary_play_snapshots (game_id, snapshot_source, snapshotted_at DESC);

CREATE INDEX IF NOT EXISTS primary_play_snapshots_date_sport_idx
  ON public.primary_play_snapshots (game_date DESC, sport);

CREATE INDEX IF NOT EXISTS primary_play_snapshots_ungraded_idx
  ON public.primary_play_snapshots (game_date DESC)
  WHERE result IS NULL;

COMMENT ON TABLE public.primary_play_snapshots IS
  'Append-only per-publish snapshots of game ensemble picks. Every game_context write + recompute writes a row. Feeds signal-level audits + drift detection.';

NOTIFY pgrst, 'reload schema';
