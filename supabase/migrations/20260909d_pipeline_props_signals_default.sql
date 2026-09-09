-- 2026-09-09: mlb_pipeline_props.signals NOT NULL default fix.
--
-- Problem: Supabase logs showed ~700+ errors/day at 16:42-16:44 UTC:
--   "null value in column signals of relation mlb_pipeline_props violates not-null constraint"
--
-- Root cause: MLB pipeline prop generator occasionally builds prop rows where
-- the signal-builder returns an empty/None signals dict (no L10 stats, no
-- lineup context, no matchup ctx — thin data paths). The column has a NOT NULL
-- constraint but no DEFAULT, so any INSERT that omits or NULLs the field is
-- rejected — dropping the prop and logging a loud error.
--
-- Fix: SET DEFAULT '{}'::jsonb on signals. NOT NULL constraint stays (so genuine
-- bugs that write NULL are still caught if you explicitly pass NULL), but omitted
-- inserts now succeed with an empty JSONB object. Downstream signal consumers
-- already handle empty dicts gracefully (they gate on `.get('key')` returning None).
--
-- Impact: kills ~700 postgres errors/day + prevents small % of daily prop drops.

ALTER TABLE mlb_pipeline_props
  ALTER COLUMN signals SET DEFAULT '{}'::jsonb;

-- Reload PostgREST schema cache so the new default is picked up immediately.
NOTIFY pgrst, 'reload schema';
