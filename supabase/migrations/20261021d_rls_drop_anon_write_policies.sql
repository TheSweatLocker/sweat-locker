-- == 2026-10-21 - RLS PART 2 - DROP THE VESTIGIAL "write anon" POLICIES ==
--
-- 20261021c enabled RLS and added read policies. It did NOT close everything,
-- and the reason is worth writing down: Part C only enabled RLS on tables
-- that did not already have it, and Part A only ADDS a SELECT policy. A table
-- that already carried an older permissive policy was therefore left exactly
-- as it was.
--
-- There are 21 such policies, named "<thing> write anon" and defined as
--
--     FOR ALL USING (true) WITH CHECK (true)
--
-- on ncaaf_game_context, ncaaf_game_results, ncaaf_team_stats,
-- ncaaf_team_aliases, the four matching ncaab_* tables, daily_grades,
-- kenpom_cache, book_lines, mlb_player_game_log and outcomes, among others.
-- FOR ALL covers INSERT, UPDATE and DELETE. So on those tables, enabling RLS
-- closed nothing at all -- the policy itself grants anon full write.
--
-- WHY THEY ARE SAFE TO DROP, measured rather than assumed:
--   149 pipeline scripts reference SUPABASE_SERVICE_ROLE_KEY
--     1 script references the anon key, and it is mlb_pipeline/verify_rls.py,
--       which this session added purely to TEST anon access
-- Nothing writes with the anon key. The policies are left over from an
-- earlier arrangement and are now pure exposure. service_role carries
-- BYPASSRLS, so the pipeline does not need a policy at all.
--
-- READS ARE PRESERVED. Every one of these tables either keeps a separate
-- "<thing> select all" FOR SELECT policy, or received an anon_read policy
-- from 20261021c Part A if the client reads it. The drop below is restricted
-- to cmd = 'ALL' precisely so a FOR SELECT policy is never removed.
--
-- The two policies this project genuinely needs for client writes are
-- cmd = 'INSERT' / 'UPDATE' on user_note_dispatch_log and user_consent, so
-- they are excluded by the cmd = 'ALL' filter and survive untouched.
--
-- Idempotent. Reports every policy it drops.

DO $$
DECLARE
  p record;
  n int := 0;
BEGIN
  FOR p IN
    SELECT schemaname, tablename, policyname, roles, cmd
      FROM pg_policies
     WHERE schemaname = 'public'
       AND cmd = 'ALL'
       -- only policies reachable by an untrusted caller. 'public' includes
       -- anon; service_role bypasses RLS regardless, so nothing the pipeline
       -- does depends on these.
       AND (roles::text[] && ARRAY['anon', 'public', 'authenticated'])
     ORDER BY tablename, policyname
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                   p.policyname, p.tablename);
    n := n + 1;
    RAISE NOTICE 'dropped FOR ALL policy %.% (roles=%)',
      p.tablename, p.policyname, p.roles;
  END LOOP;
  RAISE NOTICE 'dropped % anon-writable FOR ALL policies', n;
END $$;

-- Any table that lost its only policy above and is read by the client must
-- still have a read path. 20261021c Part A granted anon_read to every
-- relation the client reaches; this re-asserts it for the handful of tables
-- that had a FOR ALL policy and no separate FOR SELECT, so a dropped policy
-- can never leave a client read dark.
DO $$
DECLARE
  t text;
  needs_read text[] := ARRAY[
    'ncaaf_game_context', 'ncaaf_team_stats', 'ncaab_team_stats',
    'kenpom_cache', 'outcomes', 'line_history', 'public_splits_v2'
  ];
BEGIN
  FOREACH t IN ARRAY needs_read LOOP
    IF EXISTS (SELECT 1 FROM pg_class c
                 JOIN pg_namespace ns ON ns.oid = c.relnamespace
                WHERE ns.nspname = 'public' AND c.relname = t
                  AND c.relkind = 'r')
       AND NOT EXISTS (SELECT 1 FROM pg_policies
                        WHERE schemaname = 'public' AND tablename = t
                          AND cmd IN ('SELECT', 'ALL'))
    THEN
      EXECUTE format(
        'CREATE POLICY anon_read ON public.%I '
        'FOR SELECT TO anon, authenticated USING (true)', t);
      RAISE NOTICE 'restored read policy on %', t;
    END IF;
  END LOOP;
END $$;

-- == READ-ONLY AUDIT RPC ==
--
-- Verifying RLS from the outside by reading HTTP codes does not work, and
-- this session proved it twice:
--
--   * a probe using a non-existent column returns 400 PGRST204 from
--     PostgREST's own schema cache, before any SQL reaches Postgres, so it
--     says nothing about privileges;
--   * a DELETE filtered to zero rows returns 200 with an empty array whether
--     RLS denied it or it simply matched nothing -- the two are
--     indistinguishable over REST.
--
-- So the state is read from the catalog instead. SECURITY DEFINER because
-- pg_policies is filtered by the caller's rights, and read-only: it returns
-- names and flags, never row data.
CREATE OR REPLACE FUNCTION public.get_rls_audit()
RETURNS JSONB
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_catalog
STABLE
AS $$
  SELECT jsonb_build_object(
    'generated_at', now(),
    'tables', (
      SELECT jsonb_agg(jsonb_build_object(
               'table',   c.relname,
               'rls',     c.relrowsecurity,
               'forced',  c.relforcerowsecurity,
               'policies', COALESCE((
                  SELECT jsonb_agg(jsonb_build_object(
                           'name',  pol.polname,
                           -- r=SELECT w=UPDATE a=INSERT d=DELETE *=ALL
                           'cmd',   pol.polcmd,
                           'roles', (SELECT COALESCE(
                                       array_agg(pg_get_userbyid(r)), '{}')
                                     FROM unnest(pol.polroles) AS r))
                         ORDER BY pol.polname)
                  FROM pg_policy pol WHERE pol.polrelid = c.oid), '[]'::jsonb)
             ) ORDER BY c.relname)
      FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
      WHERE ns.nspname = 'public' AND c.relkind = 'r'
    )
  );
$$;

REVOKE ALL ON FUNCTION public.get_rls_audit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_rls_audit() TO service_role;

COMMENT ON FUNCTION public.get_rls_audit() IS
  'Read-only RLS posture: per public table, whether RLS is on and what '
  'policies exist (name, command, roles). service_role only. Exists because '
  'RLS state cannot be inferred from PostgREST status codes - a zero-row '
  'DELETE returns 200 whether it was denied or simply matched nothing.';

NOTIFY pgrst, 'reload schema';
