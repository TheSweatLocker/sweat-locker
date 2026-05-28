-- NBA playoff regression coefficient columns on nba_game_picks.
--
-- Trigger: 5/28 OKC@SAS picks shipped with a 19-pt model edge claim against
-- a 3.5-pt market line — Vegas does not miss playoff lines by 19. Root cause:
-- nba_picks_generator.score_game was using raw regular-season net rating gap
-- (-15.5 for OKC@SAS) directly in model_edge = nr_gap + spread. Regular-
-- season nr gaps don't extrapolate 1:1 to playoff point spreads — historical
-- correlation is ~0.35-0.50 (rotations tighten, halfcourt sets dominate,
-- both teams play stars heavier minutes).
--
-- Fix shipped same day: applies PLAYOFF_NR_REGRESSION = 0.40 to nr_gap when
-- is_playoff_time() returns true. The raw gap is preserved in the new
-- net_rating_gap_raw column so Jerry can still cite "Thunder are 15.5 pts
-- better in regular-season net rating" as descriptive color WITHOUT it
-- leaking into spread math.
--
-- playoff_regression_applied = TRUE when the regression coefficient fired.
-- Lets audit_tier_calibration distinguish regressed vs unregressed picks
-- when post-deploy backtest happens.

ALTER TABLE nba_game_picks
    ADD COLUMN IF NOT EXISTS net_rating_gap_raw      NUMERIC,
    ADD COLUMN IF NOT EXISTS playoff_regression_applied BOOLEAN DEFAULT FALSE;

NOTIFY pgrst, 'reload schema';
