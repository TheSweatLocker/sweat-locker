-- 2026-07-31c · Prop refit conviction (Option C · data-driven weight refit).
--
-- Adds refit_conviction (0-100) alongside the existing hand-tuned conviction.
-- Both columns coexist: legacy conviction is what generate_props.py emits
-- from hand-tuned scorers; refit_conviction is the post-hoc score from
-- apply_prop_refit.py using logistic-regression weights fit on 60d graded
-- outcomes (see mlb_pipeline/models/prop_refit_weights_v1.json).
--
-- Backtest lift (top-30% picks): +9.3pp system-wide, +21pp on outs_under,
-- +42pp on hits_under vs current hand-tuned ranking.
--
-- App-side reads refit_conviction with fallback to conviction — nothing
-- regresses if a prop type isn't in the refit registry yet.

ALTER TABLE mlb_pipeline_props
  ADD COLUMN IF NOT EXISTS refit_conviction NUMERIC,
  ADD COLUMN IF NOT EXISTS refit_version    TEXT;

CREATE INDEX IF NOT EXISTS idx_props_refit ON mlb_pipeline_props (game_date DESC, refit_conviction DESC NULLS LAST);

ALTER TABLE mlb_pipeline_props DISABLE ROW LEVEL SECURITY;  -- pipeline-write, no user scope

NOTIFY pgrst, 'reload schema';
