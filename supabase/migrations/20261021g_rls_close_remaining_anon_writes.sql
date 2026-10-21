-- == 2026-10-21 - RLS PART 3 - THE THREE WRITES AND ONE READ THAT SURVIVED ==
--
-- get_rls_audit() (added in 20261021d) now reports posture from the catalog
-- instead of from HTTP codes, and it found what the earlier passes missed.
-- After b through f, RLS is enabled on all 190 public tables, no client read
-- is broken, all four SECURITY INVOKER RPCs answer, and public_receipts is
-- SELECT-only. Three write policies and one read policy remain.
--
-- WHY 20261021d MISSED THEM: it filtered on cmd = 'ALL'. These are declared
-- per-command, so they are cmd 'INSERT' or 'UPDATE' and the filter skipped
-- them. Narrowing to FOR ALL was the right call then -- it guaranteed no
-- FOR SELECT policy was dropped by accident -- but it was not sufficient.
--
--   kenpom_cache.kenpom_cache_anon_write    INSERT  to anon, authenticated
--   kenpom_cache.kenpom_cache_anon_update   UPDATE  to anon, authenticated
--   outcomes.outcomes_anon_insert           INSERT  to anon, authenticated
--
-- Same justification as part 2, re-measured: 149 pipeline scripts use the
-- service-role key and exactly one uses the anon key -- the verifier added
-- this session to test anon access. Nothing writes as anon, and service_role
-- carries BYPASSRLS. Both tables keep their SELECT policies, and the client
-- reads kenpom_cache and outcomes, so reads are explicitly preserved.
--
-- AND ONE READ THAT IS NOT WHAT ITS NAME SAYS:
--
--   user_note_dispatch_log.user_read_own    SELECT  to anon, authenticated
--
-- The name reads like an ownership check. It is not one -- verified from
-- outside with the shipped anon key, a plain select returns actual rows, not
-- an empty set. That table carries user_id, 1,070 rows of who saw and
-- dismissed which note.
--
-- An ownership predicate could not work here anyway: the app makes ZERO
-- supabase.auth calls, so auth.uid() is NULL on every request and user_id is
-- never set on insert. A `user_id = auth.uid()` test would be NULL = NULL,
-- which is NULL, not true -- so either the policy is permissive (it is), or
-- it would match nothing and the name would still be wrong.
--
-- The client only ever UPSERTS this table (app/index.tsx:10685) and never
-- reads it back, so dropping the read policy is behaviour-preserving. The
-- anon_insert and anon_update policies from 20261021c stay, which is what
-- note dismissal needs.
--
-- user_consent is already closed and needs nothing: its only SELECT policy
-- (user_consent_owner_read) targets `authenticated`, and with no auth users
-- an anon select returns 0 rows. Verified rather than assumed.
--
-- Idempotent.

DROP POLICY IF EXISTS kenpom_cache_anon_write  ON public.kenpom_cache;
DROP POLICY IF EXISTS kenpom_cache_anon_update ON public.kenpom_cache;
DROP POLICY IF EXISTS outcomes_anon_insert     ON public.outcomes;

DROP POLICY IF EXISTS user_read_own ON public.user_note_dispatch_log;

-- Belt and braces: assert the read path survived on the two tables the
-- client reads, rather than trusting that a differently-named SELECT policy
-- is still there. Raises loudly instead of leaving a tab dark on a build
-- with no OTA path.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['kenpom_cache', 'outcomes'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public' AND tablename = t
                      AND cmd IN ('SELECT', 'ALL')
                      AND roles::text[] && ARRAY['anon', 'public',
                                                 'authenticated'])
    THEN
      RAISE EXCEPTION 'RLS: % has no anon SELECT policy left; the client '
                      'reads it and this would go dark', t;
    END IF;
  END LOOP;
  RAISE NOTICE 'read paths intact on kenpom_cache and outcomes';
END $$;

NOTIFY pgrst, 'reload schema';
