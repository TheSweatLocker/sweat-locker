-- detail_view: comprehensive per-fight blob for app detail page (2026-08-08)
--
-- User feedback: UFC fight detail page looked empty. Root cause — the
-- app was rendering minimal fields; the pipeline had rich data but
-- nothing packaged it for the detail view.
--
-- Per feedback_backside_dictates_app_renders: the app should render
-- one JSONB blob verbatim, no per-field lookups + no logic on frontend.
--
-- Shape (populated by ufc_detail_view.py):
--   {
--     "header": {matchup, event, fight_order, event_date, ...},
--     "verdict": {action, tier, win_pct, ev_pp, reason},
--     "odds": {a: {dec, american, implied_pct, books}, b: {...}},
--     "method": [{name, pct, bar_pct}, ...],       -- for bar chart
--     "distance": {label, pct, expected_finish_pct},
--     "rounds": [{name, pct, cumulative_pct}, ...],-- for chart
--     "fighters": {
--       a: {name, record, ko, sub, dec, finish_rate,
--           height, weight, reach, stance, age,
--           slpm, str_acc, sapm, str_def, td_avg, td_acc, td_def, sub_avg},
--       b: {...same shape...},
--     },
--     "compare": [                                  -- side-by-side stat rows
--       {label, a_val, b_val, edge, edge_side},
--     ],
--     "jerry": {short, long},
--     "meta": {computed_at, odds_pulled_at, generated_at},
--   }

ALTER TABLE public.ufc_picks
  ADD COLUMN IF NOT EXISTS detail_view JSONB;

CREATE INDEX IF NOT EXISTS ufc_picks_detail_view_idx
  ON public.ufc_picks USING gin (detail_view);

NOTIFY pgrst, 'reload schema';
