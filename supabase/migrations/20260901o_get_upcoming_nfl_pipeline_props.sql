-- get_upcoming_nfl_pipeline_props RPC (2026-09-01)
--
-- NFL prop equivalent of get_todays_pipeline_props (MLB) but week-scoped
-- since NFL is a weekly sport, not a nightly one. Returns nfl_pipeline_props
-- rows for games in the current week window (today - 1d → today + 8d).
--
-- App consumer: propJerrySport === 'NFL' branch in app/index.tsx
-- PROP_SPORT_REGISTRY.NFL will point at this RPC once wired.
--
-- Ordering: conviction DESC (matches MLB RPC) so highest-conviction
-- picks surface at top of the list.

CREATE OR REPLACE FUNCTION public.get_upcoming_nfl_pipeline_props()
RETURNS SETOF public.nfl_pipeline_props
LANGUAGE SQL
STABLE
AS $$
  SELECT *
    FROM public.nfl_pipeline_props
   WHERE game_date >= (CURRENT_DATE - INTERVAL '1 day')::DATE
     AND game_date <= (CURRENT_DATE + INTERVAL '8 days')::DATE
  ORDER BY conviction DESC NULLS LAST, game_date ASC;
$$;

GRANT EXECUTE ON FUNCTION public.get_upcoming_nfl_pipeline_props()
  TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
