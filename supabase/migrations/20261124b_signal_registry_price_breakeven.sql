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
-- A NOTE FOR WHOEVER WIRES THE READER -- AND A CORRECTION TO IT.
--
-- WHAT THIS FILE ORIGINALLY CLAIMED, on 11-24: that edge_weight returning
-- 0.0 the moment edge_pp <= 0 makes the price bar a CLIFF, that
-- ncaaf_home_field_baseline at 70.90% against a 71.39% bar sits inside its
-- own 2SE band of +/-3.3pp on n=189, and that its weight would therefore
-- swing between 0.88 and 0.00 on noise, so the reader needed a ramp.
--
-- THAT WAS WRONG ON BOTH NUMBERS, and the reader shipped on 11-25
-- (a41af348) without a ramp because it is not needed. Measured:
--
--     bar fixed at 71.39%, hit rate wiggled   |   hit fixed at 70.90%, bar wiggled
--       hr 70.90%  edge -0.49pp  w 0.0000     |     bar 70.90%  w 0.0000
--       hr 71.39%  edge +0.00pp  w 0.0000     |     bar 70.50%  w 0.0645
--       hr 71.50%  edge +0.11pp  w 0.0182     |     bar 70.00%  w 0.1393
--       hr 72.00%  edge +0.61pp  w 0.0967     |     bar 58.33%  w 0.8769
--
-- edge_weight is CONTINUOUS at the bar: edge_component is
-- 1 - exp(-edge_pp/0.06), which goes to 0 as edge_pp goes to 0 from above.
-- A 1pp wiggle in the hit rate moves the weight between 0.000 and 0.018,
-- not between 0.88 and 0.00. The 0.8769 is the weight under the FLAT bar --
-- a REGIME difference between two bars 13pp apart, not noise sensitivity.
-- And 0.5/sqrt(189) = 3.64pp is ONE SE; 2SE is 7.27pp, so the 3.3 quoted
-- above was 1SE mislabelled.
--
-- The lesson is the one this repo keeps relearning: a sentence asserting a
-- measurement is not a measurement, and a comment left unchecked becomes
-- the next person's design constraint
-- (feedback_comment_asserting_a_measurement).
--
-- WHAT THE READER ACTUALLY NEEDS, and has: the select for this column must
-- be probe-guarded. fetch_all_rows answers a PostgREST 400 by returning the
-- rows it has, which is NONE, and an empty signal_registry does not error --
-- every signal falls to UNMEASURED_FLOOR and the engine prices six sports
-- off a 0.20 constant (feedback_zero_rows_is_a_wiring_error). And a FADE
-- opinion must not inherit this bar: it is the breakeven of the prices the
-- FADED side took, and fading a -250 favourite buys a +210 dog whose bar is
-- 32%, not 71%. Reusing it would zero every fade -- the only surface we
-- have that makes money (feedback_check_which_side_a_column_indexes).
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
