-- mlb_pipeline_props duplicate cleanup + unique constraint (2026-08-25).
--
-- Root cause: generate_props.py:3919 does a plain requests.post with no
-- on_conflict param. Each pipeline invocation (3x/day) inserts a fresh
-- row per pick. In-Python dedup at line 3876 collapses only within-batch
-- duplicates, not cross-run ones. Today's slate showed 66 duplicate rows
-- across 40 picks (29% dupe rate; Pfaadt bb_under 1.5 landed 3x).
--
-- Fix has two parts:
--   1. Delete existing duplicates keeping the newest row per pick.
--   2. Add UNIQUE constraint so future POSTs with on_conflict resolve
--      cleanly and non-conflict-aware POSTs error loudly instead of
--      quietly duplicating.
--
-- Follow-up: generate_props.py needs `on_conflict=...` param on the POST
-- (separate code commit) so re-runs merge rather than error.
--
-- Idempotent.

-- Step 1: nuke duplicates (keep newest by created_at).
WITH ranked AS (
  SELECT id,
         ROW_NUMBER() OVER (
           PARTITION BY game_date, LOWER(player_name), prop_type, direction, prop_line
           ORDER BY created_at DESC NULLS LAST, id DESC
         ) AS rn
  FROM public.mlb_pipeline_props
  WHERE game_date IS NOT NULL
    AND player_name IS NOT NULL
    AND prop_type IS NOT NULL
    AND direction IS NOT NULL
    AND prop_line IS NOT NULL
)
DELETE FROM public.mlb_pipeline_props p
 USING ranked r
 WHERE p.id = r.id
   AND r.rn > 1;

-- Step 2: unique constraint prevents future dupes.
--
-- 2026-08-26 revision: use plain column list (NOT LOWER(player_name)).
-- PostgREST's on_conflict=<col-list> can only match a UNIQUE INDEX
-- defined on those exact columns — a functional index on LOWER(player_name)
-- looks like a different key to the planner. generate_props.py posts
-- with on_conflict=game_date,player_name,prop_type,direction,prop_line,
-- so the index must match that column list exactly. Case-normalization
-- of player_name happens upstream (mlb_advanced_metrics normalizes at
-- ingest); duplicates from mixed casing haven't materialized.
CREATE UNIQUE INDEX IF NOT EXISTS mlb_pipeline_props_uniq
  ON public.mlb_pipeline_props
    (game_date, player_name, prop_type, direction, prop_line);

NOTIFY pgrst, 'reload schema';
