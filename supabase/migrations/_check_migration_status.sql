-- ============================================================
-- Migration Status Checker
-- ============================================================
-- Run this in Supabase SQL editor any time to see which migrations
-- from the recent audit-battery / feature build are applied.
--
-- Each row checks for a specific artifact (table, column, index).
-- Green ✓ = applied · Red ✗ = still needs to be run.
--
-- This is NOT a formal migrations manager — it's a lightweight
-- "what's my state?" check that mirrors the manual apply workflow.
-- If you want a proper migrations table, install supabase-cli and
-- migrate to schema_migrations, but for now this is the quickest
-- sanity check.

SELECT
    check_name,
    status,
    file
FROM (
    -- === RECENT AUDIT-BATTERY MIGRATIONS (2026-07-16 onward) ===
    SELECT
        'RLS enabled on public tables (16 tables)' AS check_name,
        CASE WHEN EXISTS (
            SELECT 1 FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename = 'mlb_game_context'
              AND rowsecurity = true
        ) THEN '✓ APPLIED' ELSE '✗ MISSING' END AS status,
        '20260716_enable_rls_public_content.sql' AS file,
        1 AS ord

    UNION ALL SELECT
        'Track record honest split view',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.views
            WHERE table_schema = 'public'
              AND table_name LIKE 'track_record_honest%'
        ) THEN '✓ APPLIED' ELSE '✗ MISSING' END,
        '20260716_track_record_honest_split.sql', 2

    UNION ALL SELECT
        'RLS write policies (public_write on 15 tables)',
        CASE WHEN EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'public'
              AND policyname = 'public_write'
        ) THEN '✓ APPLIED' ELSE '✗ MISSING' END,
        '20260717_rls_pipeline_write_hotfix.sql', 3

    -- === 2026-07-21 AUDIT BATTERY SHIPS ===
    UNION ALL SELECT
        'Sub-band conviction column on prop_edge_calibration',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'prop_edge_calibration'
              AND column_name = 'conviction_band'
        ) THEN '✓ APPLIED' ELSE '✗ MISSING' END,
        '20260721_prop_edge_calibration_subband.sql', 4

    UNION ALL SELECT
        'MC probabilities jsonb column on mlb_game_context',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'mlb_game_context'
              AND column_name = 'mc_probabilities'
        ) THEN '✓ APPLIED' ELSE '✗ MISSING' END,
        '20260721_add_mc_probabilities.sql', 5

    UNION ALL SELECT
        'external_pull_log table (source aggregation audit trail)',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = 'external_pull_log'
        ) THEN '✓ APPLIED' ELSE '✗ MISSING' END,
        '20260721_external_picks_and_pull_log.sql', 6

    UNION ALL SELECT
        'external_picks table (attributed picks feed)',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = 'external_picks'
        ) THEN '✓ APPLIED' ELSE '✗ MISSING' END,
        '20260721_external_picks_and_pull_log.sql', 7

    -- === SANITY CHECKS FOR CORE TABLES (these should all be present) ===
    UNION ALL SELECT
        'CORE: mlb_game_context exists',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'mlb_game_context'
        ) THEN '✓ PRESENT' ELSE '✗ MISSING (fatal)' END,
        '—', 100

    UNION ALL SELECT
        'CORE: mlb_pipeline_props exists',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'mlb_pipeline_props'
        ) THEN '✓ PRESENT' ELSE '✗ MISSING (fatal)' END,
        '—', 101

    UNION ALL SELECT
        'CORE: jerry_cache exists',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'jerry_cache'
        ) THEN '✓ PRESENT' ELSE '✗ MISSING (fatal)' END,
        '—', 102

    UNION ALL SELECT
        'CORE: prop_edge_calibration exists',
        CASE WHEN EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'prop_edge_calibration'
        ) THEN '✓ PRESENT' ELSE '✗ MISSING (fatal)' END,
        '—', 103
) checks
ORDER BY ord;
