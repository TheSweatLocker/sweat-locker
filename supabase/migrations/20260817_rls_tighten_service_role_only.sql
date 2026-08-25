-- 🚨 LAUNCH BLOCKER RLS TIGHTENING (2026-08-17)
-- ============================================================
-- Rotates pipeline writes from anon-key wide-open (20260717 hotfix) to
-- service_role only. Anon (shipped in app bundle) becomes true READ-ONLY.
--
-- Threat closed:
--   Before: any user who extracts the anon key from the app bundle can
--           INSERT fake picks / UPDATE Jerry reads / DELETE game results.
--   After:  anon key can only SELECT. Writes require service_role key
--           (never leaves GitHub Actions secrets).
--
-- Prerequisite (must complete before applying this migration):
--   1. Add SUPABASE_SERVICE_ROLE_KEY to GitHub Actions repo secrets
--      (Supabase dashboard → Settings → API → service_role secret)
--   2. Update .github/workflows/*.yml SUPABASE_KEY assignments to
--      pull from secrets.SUPABASE_SERVICE_ROLE_KEY when available
--      (see companion workflow-yml changes in same commit)
--   3. Verify local .env still works — you'll want service_role locally
--      too for manual pipeline runs
--
-- Rollback (if anything breaks):
--   Reapply 20260717_rls_pipeline_write_hotfix.sql — recreates the
--   permissive public_write policies. Then investigate what needs
--   service_role that's still on anon.
--
-- Tables covered: same 15 as 20260717 hotfix + 6 later-added ones
-- (jerry_reads, prop_jerry_reads, feature_flags, prop_playbook_decisions,
-- ledger_suggestions, signal_sources).

DO $$
DECLARE
    tbl text;
    tables text[] := ARRAY[
        -- Original 15 from 20260717 hotfix
        'mlb_game_context', 'mlb_game_results', 'mlb_pipeline_props',
        'daily_best_bet_history', 'mlb_hr_watch', 'mlb_umpires',
        'mlb_pitcher_stats', 'mlb_team_offense', 'mlb_bullpen_stats',
        'jerry_cache', 'daily_dawg', 'mlb_line_history',
        'prop_edge_calibration', 'prop_edge_backtest_history',
        'mlb_tier_calibration',
        -- Added later — tables that had RLS fully disabled
        'jerry_reads', 'prop_jerry_reads', 'feature_flags',
        -- 2026-08-17 additions
        'prop_playbook_decisions', 'ledger_suggestions', 'signal_sources',
        -- NFL/NCAAF/NCAAB pipeline tables
        'nfl_game_context', 'nfl_game_results', 'nfl_pipeline_props',
        'ncaaf_game_context', 'ncaaf_game_results',
        'ncaab_game_context', 'ncaab_game_results',
        -- Support tables
        'signal_registry', 'external_source_track_record',
        'pipeline_health_events', 'ensemble_health',
        'ladder_state', 'ladder_rung',
        'daily_degen', 'daily_grades',
        'pattern_hits', 'sharp_scenario_game_matches'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            -- 1. Ensure RLS is enabled (some tables had it disabled via prior migrations)
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);

            -- 2. Drop the permissive public_write policy (anon wide-open)
            EXECUTE format('DROP POLICY IF EXISTS public_write ON public.%I', tbl);

            -- 3. Drop any other permissive anon write policies from later migrations
            EXECUTE format('DROP POLICY IF EXISTS anon_write ON public.%I', tbl);
            EXECUTE format('DROP POLICY IF EXISTS authenticated_write ON public.%I', tbl);

            -- 4. Ensure a public READ policy exists (anon can still read)
            EXECUTE format('DROP POLICY IF EXISTS public_read ON public.%I', tbl);
            EXECUTE format('CREATE POLICY public_read ON public.%I FOR SELECT TO anon, authenticated USING (true)', tbl);

            -- 5. Create service_role_write policy (pipeline uses this)
            EXECUTE format('DROP POLICY IF EXISTS service_role_write ON public.%I', tbl);
            EXECUTE format('CREATE POLICY service_role_write ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)', tbl);

            RAISE NOTICE '  Tightened RLS on %', tbl;
        END IF;
    END LOOP;
END $$;

-- Verify policy shape: every table should have public_read (SELECT to anon)
-- + service_role_write (ALL to service_role). Zero public write policies.
SELECT
    tablename,
    string_agg(
        format('%s [%s→%s]', policyname, cmd, roles::text),
        ', ' ORDER BY policyname
    ) AS policies
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN (
      'mlb_game_context', 'mlb_pipeline_props', 'jerry_reads',
      'signal_sources', 'prop_playbook_decisions', 'ledger_suggestions'
  )
GROUP BY tablename
ORDER BY tablename;

NOTIFY pgrst, 'reload schema';
