-- 2026-08-18: prop_pick_snapshots — immutable snapshot of each pick's
-- lines/odds/tier at CARD-LOCK time. Fixes the accountability gap where
-- book_over_odds on mlb_pipeline_props can be overwritten by later pipeline
-- runs (morning → 2pm refresh), so historical grading uses whatever odds
-- happened to be current at grade-time, not pick-time. Real yesterday example:
-- 3 of 15 shipped PRIME/STRONG props had NO book_odds captured, and my 30d
-- unit calc used flat -110 fallback which overstated PnL by ~46% (real
-- +8.49u vs assumed +15.82u for 8/17).
--
-- Snapshot is append-only per (prop_id, snapshot_source, game_date) so we
-- can capture both morning + afternoon locks separately if useful. Grading
-- writes back into result + graded_at when the game closes.
--
-- Sport-neutral schema — prop_id references mlb_pipeline_props today but
-- the same table serves NFL/NBA/NHL props by adding sport-column filters
-- once those sports ship.

BEGIN;

CREATE TABLE IF NOT EXISTS prop_pick_snapshots (
  id BIGSERIAL PRIMARY KEY,
  prop_id UUID NOT NULL,
  sport TEXT NOT NULL DEFAULT 'MLB',
  game_date DATE NOT NULL,
  snapshotted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  snapshot_source TEXT NOT NULL DEFAULT 'card_lock',
    -- card_lock = final Sharp Card commit; morning = 8am gen;
    -- afternoon = 2pm refresh; manual = ad-hoc dispatch
  -- Denormalized pick identity (survives if the source row is deleted)
  player_name TEXT,
  prop_type TEXT,
  direction TEXT,
  prop_line NUMERIC,
  matchup TEXT,
  -- LINES + ODDS at snapshot time (IMMUTABLE after insert)
  book_line NUMERIC,
  book_over_odds INT,
  book_under_odds INT,
  book_source TEXT,
  -- Scorer state at snapshot time
  legacy_tier TEXT,
  legacy_conviction NUMERIC,
  refit_conviction NUMERIC,
  playbook_tier TEXT,
  playbook_conviction NUMERIC,
  playbook_side TEXT,
  -- Grading (backfilled by grade_pick_snapshots.py once results settle)
  result TEXT,          -- 'Win' | 'Loss' | 'Push' | 'NoAction'
  graded_at TIMESTAMPTZ,
  actual_value NUMERIC, -- raw player stat vs prop_line
  unit_pnl NUMERIC      -- realized pnl at 2u sizing (PRIME/STRONG) or 1u (LEAN)
);

-- One snapshot per (prop, source, date). Deploying same-day is idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS prop_pick_snapshots_uniq
  ON prop_pick_snapshots (prop_id, snapshot_source, game_date);

-- Grading + analytics indexes
CREATE INDEX IF NOT EXISTS prop_pick_snapshots_game_date_tier
  ON prop_pick_snapshots (game_date, legacy_tier);
CREATE INDEX IF NOT EXISTS prop_pick_snapshots_result
  ON prop_pick_snapshots (game_date, result);
CREATE INDEX IF NOT EXISTS prop_pick_snapshots_playbook
  ON prop_pick_snapshots (game_date, playbook_tier)
  WHERE playbook_tier IS NOT NULL;

-- RLS (matches the service_role_write / public_read pattern from
-- 20260817_rls_tighten_service_role_only.sql)
ALTER TABLE prop_pick_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS prop_pick_snapshots_public_read ON prop_pick_snapshots;
CREATE POLICY prop_pick_snapshots_public_read
  ON prop_pick_snapshots FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS prop_pick_snapshots_service_role_write ON prop_pick_snapshots;
CREATE POLICY prop_pick_snapshots_service_role_write
  ON prop_pick_snapshots FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE prop_pick_snapshots IS
  'Immutable snapshot of each prop pick at card-lock time. Grading + unit-pnl analytics reference this table instead of mlb_pipeline_props, which can be overwritten by intra-day refresh runs. See 2026-08-18 accountability memo.';

COMMIT;

NOTIFY pgrst, 'reload schema';
