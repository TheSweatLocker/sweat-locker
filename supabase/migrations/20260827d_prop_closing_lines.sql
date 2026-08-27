-- Prop closing-line snapshot: capture the true line at T-5min for CLV.
--
-- Existing book_line/book_over_odds/book_under_odds get overwritten by
-- every sweep (multiple pulls/day). To measure "did we beat the close?"
-- we need a separate snapshot taken inside the freeze window right
-- before first pitch.
--
-- close_locked_at is set once and never cleared — idempotent guard for
-- the freeze cron running every 5-10 min.

ALTER TABLE mlb_pipeline_props
  ADD COLUMN IF NOT EXISTS close_prop_line   NUMERIC,
  ADD COLUMN IF NOT EXISTS close_over_odds   NUMERIC,
  ADD COLUMN IF NOT EXISTS close_under_odds  NUMERIC,
  ADD COLUMN IF NOT EXISTS close_locked_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS close_source      TEXT;

CREATE INDEX IF NOT EXISTS idx_mlb_pipeline_props_close_freeze
  ON mlb_pipeline_props (game_date, close_locked_at);

-- Mirror for NFL props (currently sparse but structure ready)
ALTER TABLE nfl_pipeline_props
  ADD COLUMN IF NOT EXISTS close_prop_line   NUMERIC,
  ADD COLUMN IF NOT EXISTS close_over_odds   NUMERIC,
  ADD COLUMN IF NOT EXISTS close_under_odds  NUMERIC,
  ADD COLUMN IF NOT EXISTS close_locked_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS close_source      TEXT;

CREATE INDEX IF NOT EXISTS idx_nfl_pipeline_props_close_freeze
  ON nfl_pipeline_props (game_date, close_locked_at);

NOTIFY pgrst, 'reload schema';
