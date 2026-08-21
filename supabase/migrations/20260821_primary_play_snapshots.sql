-- primary_play_snapshots — audit trail for game-level ensemble decisions.
--
-- 2026-08-21 background: mlb_game_context.primary_play is a rolling JSONB
-- overwritten on every recompute. That means when we tried to audit which
-- game signals dominated losing PRIMEs across 30d, only 12 rows survived
-- across the whole window — everything else was clobbered.
--
-- This mirrors prop_pick_snapshots (which already exists for props). Each
-- recompute writes a snapshot BEFORE overwriting ctx.primary_play, and
-- graders backfill outcomes. Weeks of history preserved for signal audits.

CREATE TABLE IF NOT EXISTS public.primary_play_snapshots (
  id             BIGSERIAL PRIMARY KEY,
  sport          TEXT NOT NULL,
  game_date      DATE NOT NULL,
  game_id        TEXT NOT NULL,
  snapshotted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  snapshot_source TEXT NOT NULL,           -- 'card_lock' | 'recompute' | 'manual'
  home_team      TEXT,
  away_team      TEXT,
  -- The full primary_play JSON at snapshot time
  primary_play   JSONB NOT NULL,
  -- Denormalized top-pick fields for query performance
  pick_type      TEXT,                     -- 'ml' | 'rl' | 'total' | 'fight'
  pick_label     TEXT,                     -- 'Philadelphia Phillies ML'
  pick_side      TEXT,                     -- 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
  pick_line      NUMERIC,
  tier           TEXT,                     -- 'PRIME' | 'STRONG' | 'LEAN'
  conviction     INT,
  score          NUMERIC,
  -- Grading (backfilled by grader)
  result         TEXT,                     -- 'W' | 'L' | 'V' (void)
  graded_at      TIMESTAMPTZ,
  actual_value   NUMERIC                   -- final total OR margin (for verification)
);

-- Unique per (game, snapshot_source) — recompute overwrites, card_lock does not
CREATE UNIQUE INDEX IF NOT EXISTS primary_play_snapshots_uniq
  ON public.primary_play_snapshots (game_id, snapshot_source);

CREATE INDEX IF NOT EXISTS primary_play_snapshots_date_sport_idx
  ON public.primary_play_snapshots (game_date DESC, sport);

CREATE INDEX IF NOT EXISTS primary_play_snapshots_ungraded_idx
  ON public.primary_play_snapshots (game_date DESC)
  WHERE result IS NULL;

COMMENT ON TABLE public.primary_play_snapshots IS
  'Immutable per-recompute snapshots of game ensemble picks. Feeds signal-level audits.';

NOTIFY pgrst, 'reload schema';
