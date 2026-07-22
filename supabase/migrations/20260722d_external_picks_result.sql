-- 2026-07-22 — Result columns for external_picks.
--
-- Vision (project_external_transparency_differentiator): grade every
-- external pick against actual outcomes → drive per-source rolling
-- calibration → drive dynamic fade tags + consensus_fade_alerts.
--
-- Populated by resolve_externals.py post-game. Sport-agnostic (result
-- format is uniform W/L/P for ml/spread/rl/total surfaces; prop grading
-- deferred to prop-resolver join).

ALTER TABLE external_picks
  ADD COLUMN IF NOT EXISTS result       TEXT,       -- 'W' | 'L' | 'P'
  ADD COLUMN IF NOT EXISTS actual_value NUMERIC,    -- total_runs for totals; margin for spreads
  ADD COLUMN IF NOT EXISTS resolved_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS resolver_note TEXT;      -- freeform tag e.g. 'game_not_found', 'prop_pending'

CREATE INDEX IF NOT EXISTS idx_external_picks_result
  ON external_picks (result) WHERE result IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_external_picks_source_result
  ON external_picks (source, sport, surface, result)
  WHERE result IS NOT NULL;

NOTIFY pgrst, 'reload schema';
