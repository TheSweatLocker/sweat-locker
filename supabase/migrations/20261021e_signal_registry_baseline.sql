-- == 2026-10-21 - SIGNAL REGISTRY - JUDGE A SIGNAL AGAINST ITS OWN PRICE ==
--
-- signal_registry stores hit_rate and sample_n, and edge_weight() turns them
-- into a signal's voice by comparing the hit rate to BREAKEVEN = 0.524, the
-- -110 breakeven. Props do not trade at -110. The publishable band here is
-- -300 to +150, where breakeven runs from 75% down to 40%.
--
-- So a flat 52.4% bar is the wrong question, and it is currently answering it
-- backwards in both directions. Measured on NHL prop history 2026-10-21,
-- inside the publishable odds band:
--
--   signal                        hit     its own b/e    verdict
--   nhl_prop_l10_extreme         59.9%       60.4%       loses by 0.5pp
--   nhl_prop_l5_cold             59.9%       60.0%       dead even
--   nhl_player_l10_extreme       58.9%       61.0%       loses by 2.0pp
--   nhl_prop_season_hit_pct_high 47.1%       53.5%       loses by 6.4pp
--   nhl_prop_l5_hot              46.2%       53.3%       loses by 7.1pp
--
-- Against 52.4% the first three look like +6 to +7pp edges and would earn
-- real weight. Against the price they actually faced, every one of them is at
-- or below water. That mislabelling is how a board produced 52 PRIME
-- decisions off signals with no demonstrable edge.
--
-- The same flaw runs the other way in refresh_prop_signal_calibration, whose
-- tier bands are absolute (VALIDATED at >= 0.55): it tiered sog_under at
-- 64.3% as VALIDATED when ~64% was simply the market price on those props.
--
-- edge_weight already accepts a `baseline` argument and floors the bar at
-- max(baseline, BREAKEVEN) -- the mechanism exists and its own comment notes
-- that `baseline` is None for every caller. This column is what lets a caller
-- pass one: the mean breakeven of the prices the signal actually fired on,
-- computed from the side the signal took (the OPPOSITE side's price for a
-- FADE, since that is the bet it implies).
--
-- Nullable with no default on purpose. NULL means "not yet measured", which
-- edge_weight already handles by falling back to BREAKEVEN. Backfilling a
-- guessed value would be indistinguishable from a measured one.

ALTER TABLE public.signal_registry
  ADD COLUMN IF NOT EXISTS baseline_pct NUMERIC;

COMMENT ON COLUMN public.signal_registry.baseline_pct IS
  'Mean breakeven win % of the prices this signal actually fired on, taken '
  'from the side the signal implies (the opposite side''s price for a FADE). '
  'Passed to edge_weight(baseline=...) so a signal is judged against the '
  'price it faced rather than a flat -110. NULL = not measured; edge_weight '
  'then falls back to BREAKEVEN 0.524.';

NOTIFY pgrst, 'reload schema';
