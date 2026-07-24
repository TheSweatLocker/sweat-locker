-- 2026-07-23 — NCAAB Phase 1 RLS enablement.
--
-- The NCAAB foundation tables (20260506d) and game_context table
-- (20260521) shipped without RLS policies. Pipeline writes via anon
-- key (SUPABASE_KEY) would 401 the moment RLS gets enforced.
--
-- Mirrors the NCAAF pattern (20260723c) — enable RLS + permissive
-- read-all + write-all-for-anon policies. Same shape as MLB anon
-- policies added in the 5c518fc hotfix.
--
-- Idempotent. Apply via Supabase SQL editor.

DO $$
BEGIN
  ALTER TABLE ncaab_team_aliases   ENABLE ROW LEVEL SECURITY;
  ALTER TABLE ncaab_team_stats     ENABLE ROW LEVEL SECURITY;
  ALTER TABLE ncaab_game_results   ENABLE ROW LEVEL SECURITY;
  ALTER TABLE ncaab_game_context   ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'RLS enable skipped: %', SQLERRM;
END $$;

DO $$
BEGIN
  DROP POLICY IF EXISTS "ncaab_aliases select all" ON ncaab_team_aliases;
  CREATE POLICY "ncaab_aliases select all" ON ncaab_team_aliases FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaab_aliases write anon" ON ncaab_team_aliases;
  CREATE POLICY "ncaab_aliases write anon" ON ncaab_team_aliases FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "ncaab_stats select all" ON ncaab_team_stats;
  CREATE POLICY "ncaab_stats select all" ON ncaab_team_stats FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaab_stats write anon" ON ncaab_team_stats;
  CREATE POLICY "ncaab_stats write anon" ON ncaab_team_stats FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "ncaab_results select all" ON ncaab_game_results;
  CREATE POLICY "ncaab_results select all" ON ncaab_game_results FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaab_results write anon" ON ncaab_game_results;
  CREATE POLICY "ncaab_results write anon" ON ncaab_game_results FOR ALL USING (true) WITH CHECK (true);

  DROP POLICY IF EXISTS "ncaab_context select all" ON ncaab_game_context;
  CREATE POLICY "ncaab_context select all" ON ncaab_game_context FOR SELECT USING (true);
  DROP POLICY IF EXISTS "ncaab_context write anon" ON ncaab_game_context;
  CREATE POLICY "ncaab_context write anon" ON ncaab_game_context FOR ALL USING (true) WITH CHECK (true);
END $$;

NOTIFY pgrst, 'reload schema';
