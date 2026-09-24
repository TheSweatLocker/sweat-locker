-- 2026-09-24  nba_pipeline_props.projection
--
-- Three NBA prop signals in signal_sources gate on p.get('projection'):
--   nba_prop_projection_supports   BACK
--   nba_prop_projection_opposes    FADE
--   nba_prop_projection_strong     BACK
-- The column they read does not exist. nba_pipeline_props has
-- `projected_value` instead, so all three are structurally dead — not
-- misconfigured, just reading a name that was never created.
--
-- It is worse than a dead read. enrich_nba_prop_projections.py sends
-- projection AND projected_value in one PATCH (the 2026-08-22 "fix" for
-- exactly this class of bug, which assumed the column existed). PostgREST
-- rejects the whole statement on an unknown column, so that patch has been
-- returning 400 PGRST204 and writing NOTHING — including the
-- `projected_value` half that worked before the fix was applied. Verified
-- 2026-09-24 against a non-matching id: both keys -> 400 PGRST204,
-- projected_value alone -> 204.
--
-- This has not shown up in production because nba_pipeline_props is empty
-- (NBA props are on hold pending minutes/logs coverage). It would have
-- shown up on opening night, 2026-10-21, as NBA props shipping with three
-- silently absent signals.
--
-- NFL is the reference shape: nfl_pipeline_props.projection is numeric and
-- carries the model's projected stat value (fixed the same day — the bridge
-- mapper had been dropping it for 1,794 of 1,796 rows). Naming NBA's column
-- the same keeps one cross-sport contract instead of a per-sport alias, so
-- the signal expressions stay portable.

ALTER TABLE public.nba_pipeline_props
  ADD COLUMN IF NOT EXISTS projection numeric;

COMMENT ON COLUMN public.nba_pipeline_props.projection IS
  'Model projected stat value for this prop. Cross-sport contract shared '
  'with nfl_pipeline_props.projection; read by the nba_prop_projection_* '
  'signal_sources expressions as p.get(''projection''). Kept in step with '
  'projected_value, which predates it and is retained for older readers.';

-- Carry over whatever projected_value already holds so the new column is
-- not born empty where a value was already known. No-op while the table is
-- empty; correct if rows land before the enrich script next runs.
UPDATE public.nba_pipeline_props
   SET projection = projected_value
 WHERE projection IS NULL
   AND projected_value IS NOT NULL;

NOTIFY pgrst, 'reload schema';
