-- 2026-07-16: Enable RLS on all public content tables.
-- ============================================================
-- Supabase has been showing a "RLS disabled on public tables"
-- warning for 7+ days — this migration resolves it.
--
-- All listed tables are pipeline OUTPUTS (game context, props, results,
-- reference stats, calibration). None contain user PII. The app currently
-- reads from anon key, so we enable RLS + add a permissive SELECT policy
-- to preserve existing access patterns. Writes come from the pipeline's
-- service role, which bypasses RLS by design.
--
-- Idempotent via DROP POLICY IF EXISTS + CREATE POLICY. Safe to re-run.

DO $$
DECLARE
    tbl text;
    tables text[] := ARRAY[
        'mlb_game_context',
        'mlb_game_results',
        'mlb_pipeline_props',
        'daily_best_bet_history',
        'mlb_hr_watch',
        'mlb_umpires',
        'mlb_pitcher_stats',
        'mlb_team_offense',
        'mlb_bullpen_stats',
        'jerry_cache',
        'daily_dawg',
        'mlb_line_history',
        'prop_edge_calibration',
        'prop_edge_backtest_history',
        'mlb_tier_calibration'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables
    LOOP
        -- Only touch tables that actually exist in public
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
            EXECUTE format('DROP POLICY IF EXISTS public_read ON public.%I', tbl);
            EXECUTE format('CREATE POLICY public_read ON public.%I FOR SELECT TO anon, authenticated USING (true)', tbl);
            RAISE NOTICE '  RLS enabled + public_read policy on %', tbl;
        ELSE
            RAISE NOTICE '  Skipped (not present): %', tbl;
        END IF;
    END LOOP;
END $$;

-- Sanity check: dump RLS status for all listed tables
SELECT
    schemaname,
    tablename,
    rowsecurity AS rls_enabled,
    (SELECT count(*) FROM pg_policies WHERE schemaname = c.schemaname AND tablename = c.tablename) AS policy_count
FROM pg_tables c
WHERE schemaname = 'public'
  AND tablename IN (
      'mlb_game_context','mlb_game_results','mlb_pipeline_props',
      'daily_best_bet_history','mlb_hr_watch','mlb_umpires',
      'mlb_pitcher_stats','mlb_team_offense','mlb_bullpen_stats',
      'jerry_cache','daily_dawg','mlb_line_history',
      'prop_edge_calibration','prop_edge_backtest_history',
      'mlb_tier_calibration'
  )
ORDER BY tablename;

NOTIFY pgrst, 'reload schema';
