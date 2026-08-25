-- NHL v1.0 launch foundation (2026-08-17).
--
-- Prepares nhl_game_context + nhl_game_results for Oct 7 season opener.
-- Adds:
--   1. Grading columns on nhl_game_results (spread_result, total_result, home_win)
--   2. L5 team tendency columns on nhl_game_context (ATS, O/U, ML)
--      Window is L5 for NHL — 82-game season, 5 games = ~2 weeks of form
--
-- Pattern ported from MLB/NFL/NCAAF tendencies work (Aug 17).
-- Ready for preseason data flow starting late September.

-- ─── nhl_game_results: grading fields ──────────────────────────────────
-- NHL scoring: 3-way (home wins reg/OT/SO or away wins reg/OT/SO). Puckline
-- is typically -1.5/+1.5; totals typically 5.5 or 6.5. spread_result and
-- total_result computed by resolver.
ALTER TABLE public.nhl_game_results
  ADD COLUMN IF NOT EXISTS close_puckline        NUMERIC,
  ADD COLUMN IF NOT EXISTS close_total           NUMERIC,
  ADD COLUMN IF NOT EXISTS close_home_ml         INT,
  ADD COLUMN IF NOT EXISTS close_away_ml         INT,
  ADD COLUMN IF NOT EXISTS spread_result         TEXT,   -- 'home_covered' | 'away_covered' | 'push'
  ADD COLUMN IF NOT EXISTS total_result          TEXT,   -- 'over' | 'under' | 'push'
  ADD COLUMN IF NOT EXISTS home_win              BOOLEAN,
  ADD COLUMN IF NOT EXISTS went_to_ot            BOOLEAN,
  ADD COLUMN IF NOT EXISTS went_to_so            BOOLEAN,
  ADD COLUMN IF NOT EXISTS total_goals           INT,
  ADD COLUMN IF NOT EXISTS resolved_at           TIMESTAMPTZ;

-- ─── nhl_game_context: L5 team tendency columns ────────────────────────
ALTER TABLE public.nhl_game_context
  ADD COLUMN IF NOT EXISTS home_ats_last5              INT,
  ADD COLUMN IF NOT EXISTS home_ats_last5_losses       INT,
  ADD COLUMN IF NOT EXISTS away_ats_last5              INT,
  ADD COLUMN IF NOT EXISTS away_ats_last5_losses       INT,
  ADD COLUMN IF NOT EXISTS home_ou_last5_overs         INT,
  ADD COLUMN IF NOT EXISTS home_ou_last5_unders        INT,
  ADD COLUMN IF NOT EXISTS away_ou_last5_overs         INT,
  ADD COLUMN IF NOT EXISTS away_ou_last5_unders        INT,
  ADD COLUMN IF NOT EXISTS home_covers_as_fav_pct      NUMERIC,
  ADD COLUMN IF NOT EXISTS home_covers_as_dog_pct      NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_fav_pct      NUMERIC,
  ADD COLUMN IF NOT EXISTS away_covers_as_dog_pct      NUMERIC,
  ADD COLUMN IF NOT EXISTS home_ml_last5               INT,
  ADD COLUMN IF NOT EXISTS home_ml_last5_losses        INT,
  ADD COLUMN IF NOT EXISTS away_ml_last5               INT,
  ADD COLUMN IF NOT EXISTS away_ml_last5_losses        INT,
  ADD COLUMN IF NOT EXISTS team_tendencies_updated_at  TIMESTAMPTZ,
  -- Ensemble output slot (matches MLB/NFL pattern)
  ADD COLUMN IF NOT EXISTS primary_play                JSONB;

-- Grant tightened-RLS-compatible policies (2026-08-17 rls_tighten migration
-- pattern). These tables predate that migration; retrofit here.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='nhl_game_context') THEN
    ALTER TABLE public.nhl_game_context ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS public_read ON public.nhl_game_context;
    CREATE POLICY public_read ON public.nhl_game_context FOR SELECT TO anon, authenticated USING (true);
    DROP POLICY IF EXISTS service_role_write ON public.nhl_game_context;
    CREATE POLICY service_role_write ON public.nhl_game_context FOR ALL TO service_role USING (true) WITH CHECK (true);
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='nhl_game_results') THEN
    ALTER TABLE public.nhl_game_results ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS public_read ON public.nhl_game_results;
    CREATE POLICY public_read ON public.nhl_game_results FOR SELECT TO anon, authenticated USING (true);
    DROP POLICY IF EXISTS service_role_write ON public.nhl_game_results;
    CREATE POLICY service_role_write ON public.nhl_game_results FOR ALL TO service_role USING (true) WITH CHECK (true);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
