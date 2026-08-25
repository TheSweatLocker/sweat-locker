-- UFC sharp-fade experiment (2026-08-17).
--
-- Adds experiment_flag column on ufc_picks so we can filter/aggregate
-- picks that came from the sharp-fade experiment path vs normal model
-- picks. Enables clean backtest: how did sharp-fade PRIMEs do vs
-- historical model PRIMEs?
--
-- See project_ufc_model_broken_817 memo for background: model 85%+
-- band hits 40% actual, so fading should hit ~60%. This experiment
-- validates that math over next 2-3 fight cards.

ALTER TABLE public.ufc_picks
  ADD COLUMN IF NOT EXISTS experiment_flag TEXT;

CREATE INDEX IF NOT EXISTS idx_ufc_picks_experiment
  ON public.ufc_picks (experiment_flag)
  WHERE experiment_flag IS NOT NULL;

NOTIFY pgrst, 'reload schema';
