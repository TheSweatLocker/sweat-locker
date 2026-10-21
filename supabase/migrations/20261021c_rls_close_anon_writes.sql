-- == 2026-10-21 - RLS - CLOSE ANON WRITE ACCESS TO THE PUBLIC SCHEMA ==
--
-- THE FINDING, VERIFIED FROM OUTSIDE WITH THE SHIPPED APP KEY:
-- RLS is disabled across the public schema, so the anon key -- which ships
-- inside the app bundle and can be read out of any installed copy -- is not
-- limited to reading. It can WRITE. Confirmed by issuing DELETE and PATCH
-- with a filter matching zero rows (so nothing was altered) and getting
-- 200/OK rather than 401/403 on:
--
--     public_receipts          <-- THE PUBLIC RECORD
--     prompt_templates         <-- the LLM prompts
--     feature_flags            <-- what the app turns on
--     sweat_tier_config        <-- the tier gates
--     user_note_dispatch_log   <-- carries user_id
--
-- public_receipts is the whole "we show the receipts" brand. Anyone able to
-- UPDATE it can silently rewrite the graded record. That is the finding that
-- matters; the readable-data part is secondary.
--
-- A NOTE ON HOW THIS WAS MEASURED, because the first attempt was wrong.
-- Probing with a non-existent column returns 400 PGRST204, and that proves
-- NOTHING: PGRST204 comes from PostgREST own schema cache, which rejects the
-- payload before any SQL reaches Postgres. A privilege test has to actually
-- reach the database. Hence the zero-row DELETE/PATCH above.
--
-- WHY THIS IS SAFE TO APPLY TO A LIVE APP WITH NO OTA PATH:
-- enabling RLS and granting a permissive SELECT policy is read-NEUTRAL and
-- write-CLOSING. Reads that work today keep working byte for byte; the
-- INSERT/UPDATE/DELETE nobody should have had are denied, because RLS
-- default-denies any command with no matching permissive policy. The
-- pipeline is unaffected throughout: the service-role key bypasses RLS
-- entirely, and the one edge function (odds-proxy) uses service role.
--
-- THE RELATION LIST IS DERIVED, NOT TYPED. Part A is every relation the
-- client reaches via .from() in app/, plus ufc_picks, which appears in NO
-- .from() call and is reachable only through get_todays_ufc_picks(). That
-- matters: all four client RPCs (get_todays_sweat_card, _potd, _dawg,
-- _ufc_picks) carry no SECURITY DEFINER clause, so they run as SECURITY
-- INVOKER -- with the CALLER privileges. A table reached only through one of
-- them still needs a read policy or the tab goes dark, and it would go dark
-- on a build that cannot be hot-fixed.
--
-- Seven of the listed relations are views (v_*). Views have no RLS of their
-- own and ALTER TABLE ... ENABLE ROW LEVEL SECURITY on one is an error, so
-- every loop below checks pg_class.relkind and skips anything that is not an
-- ordinary table. That is why the list is applied through a DO block rather
-- than written out as 78 literal statements.
--
-- Idempotent: safe to re-run. Policies are dropped by name first.

-- -- A. RELATIONS THE CLIENT LEGITIMATELY READS ---------------------------
-- RLS on + permissive SELECT. No write policy, so writes close.
DO $$
DECLARE
  t text;
  kind "char";
  n_tbl int := 0; n_view int := 0; n_missing int := 0;
  read_only text[] := ARRAY[
    'admin_notice', 'cleatz_signals', 'cohort_display_config',
    'cohort_tag_records', 'config_ui_sections', 'daily_best_bet_history',
    'daily_dawg', 'daily_degen', 'daily_surface_records', 'external_picks',
    'external_source_calibration', 'external_source_track_record',
    'fadereport_signals', 'feature_flags', 'home_banners', 'jerry_cache',
    'jerry_reads', 'kenpom_cache', 'ladder_rung', 'ladder_state',
    'ledger_snapshots', 'ledger_suggestions', 'line_history',
    'line_movement_flags', 'mlb_game_context', 'mlb_game_results',
    'mlb_hr_watch', 'mlb_pipeline_props', 'mlb_pitcher_stats',
    'mlb_tier_calibration', 'mlb_umpires', 'nba_game_context',
    'nba_injuries', 'nba_playoff_series', 'nba_team_stats',
    'ncaab_team_stats', 'ncaaf_game_context', 'ncaaf_team_stats',
    'nfl_game_context', 'nfl_injuries', 'nfl_player_projections',
    'nfl_starters', 'nfl_team_stats', 'nhl_game_context',
    'nhl_pipeline_props', 'odds_cache', 'outcomes', 'pitcher_projections',
    'prompt_templates', 'prop_grades', 'prop_jerry_cache',
    'prop_jerry_reads', 'public_splits_v2', 'scenario_audit',
    'sharp_agreement_calibration', 'sharp_source_calibration',
    'signal_pattern_registry', 'sport_registry', 'surface_records',
    'sweat_tier_config', 'team_recent_games', 'team_situational_records',
    'team_stats_rolling', 'tier_integrity_findings', 'ufc_fighter_stats',
    'ufc_picks', 'ufc_upcoming_event', 'ui_notes', 'user_notes',
    'v_dawg_track_record_by_tier', 'v_mlb_props_publishable',
    'v_nfl_props_publishable', 'v_prop_track_record',
    'v_prop_track_record_by_type', 'v_signal_records',
    'v_signal_track_record'
  ];
BEGIN
  FOREACH t IN ARRAY read_only LOOP
    SELECT c.relkind INTO kind
      FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
     WHERE ns.nspname = 'public' AND c.relname = t;

    IF kind IS NULL THEN
      n_missing := n_missing + 1;
      RAISE WARNING 'RLS: relation does not exist, skipped: %', t;
    ELSIF kind = 'r' THEN
      EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('DROP POLICY IF EXISTS anon_read ON public.%I', t);
      EXECUTE format(
        'CREATE POLICY anon_read ON public.%I '
        'FOR SELECT TO anon, authenticated USING (true)', t);
      n_tbl := n_tbl + 1;
    ELSE
      -- view / matview: no RLS of its own; governed by its base tables
      n_view := n_view + 1;
    END IF;
  END LOOP;

  RAISE NOTICE 'A: % tables secured, % views skipped, % missing',
    n_tbl, n_view, n_missing;
END $$;

-- prompt_templates: the client already filters is_active, so matching that in
-- the policy is exactly equivalent for the app and strictly tighter for
-- everyone else. It is NOT a real fix. The app reads the prompt text to
-- render it on-device, so active templates must reach the client no matter
-- what the policy says; RLS cannot hide what the client renders. Closing that
-- properly means moving prompt rendering behind an edge function, which is an
-- architecture change and does not belong in a security migration.
DROP POLICY IF EXISTS anon_read ON public.prompt_templates;
CREATE POLICY anon_read ON public.prompt_templates
  FOR SELECT TO anon, authenticated USING (is_active);

-- -- B. THE TWO RELATIONS THE CLIENT WRITES -------------------------------
-- Both are append-only from the device point of view, and NEITHER is ever
-- read back by the app. Verified -- the only .from() calls are
--   user_note_dispatch_log  -> .upsert(...)   app/index.tsx:10685
--   user_consent            -> .insert(...)   app/index.tsx:13023
-- so granting write WITHOUT read is behaviour-preserving, and it closes the
-- 1,070-row user_id exposure on the dispatch log. The upsert needs UPDATE as
-- well as INSERT to resolve its ON CONFLICT.
ALTER TABLE public.user_note_dispatch_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS anon_insert ON public.user_note_dispatch_log;
DROP POLICY IF EXISTS anon_update ON public.user_note_dispatch_log;
DROP POLICY IF EXISTS anon_read   ON public.user_note_dispatch_log;
CREATE POLICY anon_insert ON public.user_note_dispatch_log
  FOR INSERT TO anon, authenticated WITH CHECK (true);
CREATE POLICY anon_update ON public.user_note_dispatch_log
  FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);

ALTER TABLE public.user_consent ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS anon_insert ON public.user_consent;
DROP POLICY IF EXISTS anon_read   ON public.user_consent;
CREATE POLICY anon_insert ON public.user_consent
  FOR INSERT TO anon, authenticated WITH CHECK (true);

-- -- C. EVERYTHING ELSE IN public: RLS ON, NO POLICY = DENY ANON ----------
-- Whatever is left is a table no client code path touches. RLS with no policy
-- denies anon every command while leaving service-role (the whole pipeline)
-- untouched. Runs last, and only on tables that do not already have RLS, so
-- it cannot disturb A or B.
--
-- ASSUMPTION WORTH STATING: the only anon-key consumer is this app. If
-- anything else ever reads Supabase with the anon key it needs a policy here.
-- Nothing in the repo does today.
DO $$
DECLARE r record; n int := 0; n_skip int := 0;
BEGIN
  FOR r IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
     WHERE ns.nspname = 'public' AND c.relkind = 'r'
       AND NOT c.relrowsecurity
     ORDER BY c.relname
  LOOP
    -- Per-table exception handling on purpose. An extension-owned table in
    -- public (PostGIS spatial_ref_sys is the classic one) raises "must be
    -- owner of table", and without this block that single failure would
    -- abort the whole DO and silently leave every table after it in the
    -- ordering wide open. Skip the one we cannot alter, keep going, and say
    -- which it was.
    BEGIN
      EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',
                     r.relname);
      n := n + 1;
      RAISE NOTICE 'C: deny-anon (no policy): %', r.relname;
    EXCEPTION WHEN insufficient_privilege OR wrong_object_type THEN
      n_skip := n_skip + 1;
      RAISE WARNING 'C: could not enable RLS on % (%), SKIPPED - check it '
                    'by hand', r.relname, SQLERRM;
    END;
  END LOOP;
  RAISE NOTICE 'C: % further tables closed, % skipped', n, n_skip;
END $$;

NOTIFY pgrst, 'reload schema';
