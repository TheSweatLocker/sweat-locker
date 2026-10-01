-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-01 · NHL puck-line + total PRICES
--
-- WHY
-- ---
-- No results table in this database stores a spread price or a total price.
-- Only moneylines carry one. compute_surface_records therefore graded every
-- rl and total pick at a flat -110.
--
-- For a football spread that approximation is close to true. For an NHL puck
-- line it is badly wrong: +/-1.5 is a huge cushion in a ~6.1-goal game, so it
-- trades near -250 on the favourite and +190 on the dog.
--
-- That single missing number is what blocks the NHL engine. Measured
-- 2026-10-01 (project_nhl_engine_one_sided_1001): the ensemble's rl lens
-- returned None on 34 of 34 games and NHL has NEVER published a puck line or
-- a total, because all ten rl-scope signals sit UNVALIDATED. A replay grades
-- four of them at 70-72% on n=585-811 against the correct 65.2% baseline --
-- a real +5.5 to +7.3pp lift -- but a lift in hit-rate POINTS is not a
-- positive ROI, and at -250 juice a 72% winner still loses money. Without the
-- price there is no way to tell those two cases apart, which is precisely the
-- artifact that made NCAAF COVERAGE read 60.5% at NEGATIVE ROI
-- (project_ncaaf_ml_path_is_the_leak_930).
--
-- The Odds API response ALREADY carried these prices. odds_pull_core
-- _consensus() read `price` off every outcome and kept it only for h2h,
-- discarding it for spreads and totals. So this costs no extra API calls and
-- no extra quota -- it stores a number we were already fetching and throwing
-- away.
--
-- SCOPE: NHL only, deliberately. The puller takes the column map as opt-in
-- (price_cols), so MLB/NFL/NCAAF/NBA are untouched until their own migration
-- lands. A results table missing the column would 400 the whole upsert.
--
-- FORWARD-ONLY. These columns populate from the next nhl_odds_pull run on.
-- The 2,936 historical scored games will stay NULL -- we have no historical
-- puck-line odds source -- so the rl signals cannot be ROI-validated
-- retroactively. They become validatable as forward priced samples
-- accumulate. Do not read NULL here as "no juice".
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.nhl_game_results
  ADD COLUMN IF NOT EXISTS close_puckline_home_price integer,
  ADD COLUMN IF NOT EXISTS close_puckline_away_price integer,
  ADD COLUMN IF NOT EXISTS close_total_over_price    integer,
  ADD COLUMN IF NOT EXISTS close_total_under_price   integer;

COMMENT ON COLUMN public.nhl_game_results.close_puckline_home_price IS
  'American odds on the HOME puck line at close_puckline. Median across books '
  'quoting that exact line. NHL -1.5 typically -200..-300. Forward-only from '
  '2026-10-01; NULL on historical rows means unknown, not -110.';
COMMENT ON COLUMN public.nhl_game_results.close_puckline_away_price IS
  'American odds on the AWAY puck line (negation of close_puckline). '
  'Typically +150..+220 on +1.5. Forward-only from 2026-10-01.';
COMMENT ON COLUMN public.nhl_game_results.close_total_over_price IS
  'American odds on OVER at close_total. Forward-only from 2026-10-01.';
COMMENT ON COLUMN public.nhl_game_results.close_total_under_price IS
  'American odds on UNDER at close_total. Forward-only from 2026-10-01.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — read it back, do not trust the DDL succeeding.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT count(*)                            AS rows,
--        count(close_puckline)                AS have_pl_line,
--        count(close_puckline_home_price)     AS have_pl_home_px,
--        count(close_puckline_away_price)     AS have_pl_away_px,
--        count(close_total_over_price)        AS have_tot_over_px,
--        min(close_puckline_home_price)       AS pl_home_px_min,
--        max(close_puckline_away_price)       AS pl_away_px_max
--   FROM public.nhl_game_results
--  WHERE game_date >= '2026-10-01';
