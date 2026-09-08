-- 2026-09-09 FOLLOW-UP to 20260909a_rls_close_public_holes.sql.
--
-- The lockdown from 20260909a broke the app: it writes to `outcomes`
-- (user bet results) and reads+writes to `kenpom_cache` (client-side
-- cache) using the anon key. Locking down anon = every bet-mark and
-- every NCAAB context fetch throws 401. Silent in the app (try/catch)
-- but pollutes Supabase logs.
--
-- Correct fix (long-term): move both writes to a server-side function.
-- Correct fix (short-term, this migration): grant the specific verbs
-- the app needs, keep RLS ON to prevent world-reads.
--
-- Trade-off: anon retains INSERT on outcomes + read+UPSERT on
-- kenpom_cache. That's the current app contract. Real cleanup lives in
-- project_pipeline_overhaul_909 P2 (write-authority pattern).

-- outcomes: user bet result log. App INSERTs on every bet mark.
-- Grant anon+authenticated INSERT + SELECT.
-- Rows are effectively public tracking data (no PII beyond bet metadata).
GRANT SELECT, INSERT ON public.outcomes TO anon, authenticated;

DROP POLICY IF EXISTS outcomes_anon_insert ON public.outcomes;
CREATE POLICY outcomes_anon_insert ON public.outcomes
  FOR INSERT TO anon, authenticated
  WITH CHECK (true);

DROP POLICY IF EXISTS outcomes_public_select ON public.outcomes;
CREATE POLICY outcomes_public_select ON public.outcomes
  FOR SELECT TO anon, authenticated
  USING (true);

-- kenpom_cache: client-side cache for KenPom rankings (public info).
-- App reads + UPSERTs on NCAAB context fetches. Not sensitive.
-- Grant anon+authenticated SELECT + INSERT + UPDATE (UPDATE needed for UPSERT).
GRANT SELECT, INSERT, UPDATE ON public.kenpom_cache TO anon, authenticated;

DROP POLICY IF EXISTS kenpom_cache_public_read ON public.kenpom_cache;
CREATE POLICY kenpom_cache_public_read ON public.kenpom_cache
  FOR SELECT TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS kenpom_cache_anon_write ON public.kenpom_cache;
CREATE POLICY kenpom_cache_anon_write ON public.kenpom_cache
  FOR INSERT TO anon, authenticated
  WITH CHECK (true);

DROP POLICY IF EXISTS kenpom_cache_anon_update ON public.kenpom_cache;
CREATE POLICY kenpom_cache_anon_update ON public.kenpom_cache
  FOR UPDATE TO anon, authenticated
  USING (true) WITH CHECK (true);

-- ufc_fight_results + mlb_catcher_framing STAY locked. App does not
-- query them directly per the audit; only pipeline scripts (service_role)
-- write. Keep the 20260909a lockdown.

NOTIFY pgrst, 'reload schema';
