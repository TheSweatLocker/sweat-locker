-- 20261124b · signal_registry.price_breakeven_pct
--
-- Andy 2026-11-24, on the self-regulating engine he has asked for repeatedly:
-- make the loop measure the signal, at the price, instead of restating our
-- own picks.
--
-- THE GAP. refit_signal_registry.build_rows already computes `be_frac` --
-- the mean breakeven of the prices a signal ACTUALLY FIRED AT, gated on 80%
-- coverage of its firings -- and passes it to _tier_for, so the TIER has been
-- price-aware in design since 2026-10-29. It then discards the number. No
-- column holds it.
--
-- So ensemble_scorer, the only live producer of pick weight, calls
--     edge_weight(hit_rate, n, tier, baseline=side_baseline(...),
--                 breakeven=market_breakeven(market))
-- where market_breakeven is FLAT: ml 0.5833, rl/total 0.5238. The bar is
-- max(side_baseline, flat) and never the price. A signal firing at -250 is
-- judged against 58.33% when its real bar is 71.4%. This is the same shape as
-- recommended_weight: computed correctly, written where nothing reads it.
--
-- AND IT HAD NEVER RUN AT ALL. _firing_breakeven reads the closing price off
-- the results row, and NEITHER of collect_game_firings' two explicit SELECTs
-- asked for a price column, so it read None on every firing and be_n stayed 0
-- for all six sports -- 0 of 91 signal-market cells at n>=20 had a measured
-- breakeven (feedback_explicit_select_silent_blanks). Fixed in the same
-- commit as this file, along with a second defect: the column names differ by
-- sport, and ncaab_game_results carries BOTH spellings with the one the code
-- asked for 0.0% filled and the other 74.2%, so the names are now resolved by
-- FILL rather than by presence
-- (feedback_a_zero_fill_column_can_have_a_populated_twin).
--
-- WHAT THE FIX MEASURES, once the select was corrected, over 120 days:
--
--     51 of 91 signal-market cells (56%) now have a measured price breakeven
--     17 signals lose weight against it, 5 go to ZERO, 3 gain
--     summed cell weight 17.81 -> 11.06
--
--     NCAAF ncaaf_home_field_baseline  ml  n=189  hit 70.90%
--         blind baseline 57.60%  flat bar 58.33%  OWN PRICE BAR 71.39%
--         weight 0.8769 -> 0.0000
--     NCAAF ncaaf_confluence_home      ml  n=123  hit 71.54%  bar 74.89%
--         weight 0.8895 -> 0.0000
--     NCAAF ncaaf_projected_spread     ml  n=187  hit 72.73%  bar 71.68%
--         weight 0.9092 -> 0.1601
--     NFL   power_diff_edge            ml  n= 41  hit 78.05%  bar 74.79%
--         weight 0.7796 -> 0.3391
--
-- On the live 2026-11-24 slate, 26.6% of the weight behind picked sides has a
-- measured breakeven, and that weight falls 57.3% (40.36 -> 17.23). It is the
-- same finding the published record shows from the other direction:
-- market=ml hits 62.7% against a 59.4% implied and returns -0.0%.
--
-- WHAT THIS MIGRATION DOES NOT DO. It changes no pick. It adds a column so
-- the number can be STORED; refit_signal_registry writes it behind a presence
-- probe (_have_price_be_col) and omits the key entirely until this is applied,
-- because an unknown key is a PostgREST 400 that would take the nightly refit
-- down for every sport. Teaching edge_weight to USE it is a separate change,
-- behind its own flag and a shadow period, because it rescores every sport.
--
-- A NOTE FOR WHOEVER WIRES THE READER. edge_weight returns 0.0 the moment
-- edge_pp <= 0, so the price bar is a CLIFF: ncaaf_home_field_baseline at
-- 70.90% against a 71.39% bar is inside its own 2SE band (+/-3.3pp on n=189)
-- and would swing between 0.88 and 0.00 on noise. The reader should ramp
-- across the bar, or allow an SE margin, rather than inherit that step.
--
-- No NOTIFY pgrst: it makes Andy's SQL client report 42P01 relation "a", and
-- Supabase ships a DDL event trigger that reloads the schema itself
-- (feedback_notify_pgrst_breaks_andys_sql_client).

ALTER TABLE public.signal_registry
  ADD COLUMN IF NOT EXISTS price_breakeven_pct DOUBLE PRECISION;

COMMENT ON COLUMN public.signal_registry.price_breakeven_pct IS
  'Mean breakeven win rate, in PERCENT, of the closing prices this signal '
  'actually fired at, over its graded firings for this market_scope. '
  'Written by refit_signal_registry.build_rows when >= 80% of firings carry '
  'a price, else the per-market median fallback. This is the bar the signal '
  'has to beat to make money -- NOT the side baseline (baseline_pct, which '
  'is what it has to beat to carry information) and NOT the flat '
  'market breakeven the scorer currently uses. 20261124b.';
