-- 2026-09-15 · project_nfl_k_pts_calibration_bug_912
--
-- Ship-alongside column for the recalibrated NFL projected_spread.
-- The legacy `projected_spread` uses K_PTS=0.15 which collapses every
-- game's output to HFA ± noise (2.42pt range vs market's 35pt range).
-- The market anchor (added 2026-09-13, da0e9778) masks this by pulling
-- every game toward market, so the raw formula looks like it's working
-- but has no predictive spread of its own.
--
-- projected_spread_v2 uses model_pred_home_points - model_pred_away_points
-- as the basis (regression on 315 games → correlation 0.51 with market
-- vs 0.46 for off_rating_diff basis). Ship-alongside so ensemble +
-- anchor readers keep reading legacy `projected_spread` until we
-- validate v2 over a graded window.
--
-- No RLS change (inherits parent table policy).

ALTER TABLE public.nfl_game_context
    ADD COLUMN IF NOT EXISTS projected_spread_v2 NUMERIC;

COMMENT ON COLUMN public.nfl_game_context.projected_spread_v2 IS
    'Ship-alongside candidate to replace projected_spread. Formula: '
    '0.80 × (model_pred_home_points - model_pred_away_points) + 0.90. '
    'Regressed on 315 games (r=0.51 vs market). Legacy projected_spread '
    'stays primary until validation window closes.';

NOTIFY pgrst, 'reload schema';
