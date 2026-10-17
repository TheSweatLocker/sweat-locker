-- ══ 2026-10-17 · MAKE THE XGBOOST SUPPRESSION MEASURABLE ══
-- ANDY: "V4 model is missing in mlb game today, did engine not make best pick
-- base don that missing?"
--
-- It was not missing. game_context.py suppressed it: XGBoost said ~11.2 runs,
-- the v3 formula said 6.4, and DISAGREEMENT_THRESHOLD = 4.0 drops the model
-- whenever they differ by 4+, on the assumption that a big gap means an
-- XGBoost blind spot. Every other MLB date this month cleared the gate easily
-- (10-15 deltas 0.29 and 0.74), which is why model_pred_* looked healthy right
-- up to this matchup.
--
-- THE DEFECT IS NOT THE GUARD, IT IS THAT THE GUARD IS UNFALSIFIABLE.
-- When it fires, model_pred_* is written NULL and the suppressed number is
-- thrown away. So the question "when these two disagree by 4+, which one was
-- closer to the actual total?" cannot be answered from stored data at all --
-- not for today, and not for any of the suppressions already behind us. The
-- code even documents a case (SF@CHC 6/6) where v3 was the outlier and the
-- guard killed the right model anyway, and the threshold was moved 2.5 -> 4.0
-- on that single anecdote, because an anecdote was the only evidence available.
--
-- This stores what was suppressed and why. It changes NO published pick: the
-- shadow columns are written alongside, and model_pred_* keeps its current
-- behaviour exactly. After a few weeks of rows we can grade suppressed-XGBoost
-- against v3 against the actual total and decide the threshold on evidence
-- instead of on a story.
--
-- Same pattern already used for the NHL suppression gate: shadow first, exit
-- on evidence, never flip a live gate on one game.

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS xgb_shadow_total        numeric,
  ADD COLUMN IF NOT EXISTS xgb_shadow_spread       numeric,
  ADD COLUMN IF NOT EXISTS xgb_shadow_home_runs    numeric,
  ADD COLUMN IF NOT EXISTS xgb_shadow_away_runs    numeric,
  ADD COLUMN IF NOT EXISTS xgb_suppress_reason     text,
  ADD COLUMN IF NOT EXISTS xgb_model_version       text;

COMMENT ON COLUMN mlb_game_context.xgb_shadow_total IS
  'XGBoost total as predicted, stored even when the disagreement/xERA guard '
  'suppressed it from model_pred_total. Shadow only - never published.';
COMMENT ON COLUMN mlb_game_context.xgb_suppress_reason IS
  'Why model_pred_* is NULL: disagreement_4.0 | missing_xera | not_loaded | '
  'predict_failed | NULL when the model was actually used.';

-- the snapshot table mirrors context, so it carries the shadow too
ALTER TABLE mlb_game_context_snapshots
  ADD COLUMN IF NOT EXISTS xgb_shadow_total    numeric,
  ADD COLUMN IF NOT EXISTS xgb_shadow_spread   numeric,
  ADD COLUMN IF NOT EXISTS xgb_suppress_reason text;

NOTIFY pgrst, 'reload schema';
