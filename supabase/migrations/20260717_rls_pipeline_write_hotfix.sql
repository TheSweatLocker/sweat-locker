-- 2026-07-17 HOTFIX: RLS was blocking pipeline writes.
-- ============================================================
-- Yesterday's RLS migration (20260716_enable_rls_public_content.sql)
-- added SELECT-only policies. The pipeline (GitHub Actions) writes using
-- the anon key, so every INSERT/UPSERT started returning:
--   401 "new row violates row-level security policy"
--
-- Consequence: 7/17 AM cron uploaded 0 pitcher_stats, 0 bullpen_stats,
-- 0 game_context rows. Full slate blackout — no card, no POTD.
--
-- Fix: add permissive INSERT/UPDATE/DELETE policies for anon on all
-- pipeline write targets. RLS stays ENABLED (Supabase warning stays
-- resolved) but writes are now permitted. Same effective posture we
-- had before, just with the "enable RLS" flag flipped.
--
-- Proper follow-up (next week): rotate the pipeline to a service_role
-- key so anon truly is read-only. Add that as HIGH priority ticket.
--
-- Idempotent (DROP POLICY IF EXISTS + CREATE POLICY).

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
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            EXECUTE format('DROP POLICY IF EXISTS public_write ON public.%I', tbl);
            EXECUTE format('CREATE POLICY public_write ON public.%I FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', tbl);
            RAISE NOTICE '  Write policy added on %', tbl;
        END IF;
    END LOOP;
END $$;

-- Verify all 15 tables have both read + write policies
SELECT
    tablename,
    (SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = t.tablename AND cmd = 'SELECT') AS select_policies,
    (SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = t.tablename AND (cmd = 'ALL' OR cmd IN ('INSERT','UPDATE','DELETE'))) AS write_policies
FROM (
    SELECT unnest(ARRAY[
        'mlb_game_context','mlb_game_results','mlb_pipeline_props',
        'daily_best_bet_history','mlb_hr_watch','mlb_umpires',
        'mlb_pitcher_stats','mlb_team_offense','mlb_bullpen_stats',
        'jerry_cache','daily_dawg','mlb_line_history',
        'prop_edge_calibration','prop_edge_backtest_history',
        'mlb_tier_calibration'
    ]) AS tablename
) t
ORDER BY tablename;

NOTIFY pgrst, 'reload schema';
