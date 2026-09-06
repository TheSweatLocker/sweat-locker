-- 2026-09-06: NFL Week 1 (opener Wed 9/10) and NCAAF Week 2 are live.
-- The 2026-08-13 seed migration set NFL/NCAAF into 'preseason' state with
-- launch-date state_messages ("Regular season kicks off Sept 4",
-- "NCAAF season kicks off Aug 22 · Week 0 preview"). Those messages now
-- render as stale banners on the Games tab even though both sports are
-- playing in-season games.
--
-- Also per user directive (see [[project_faq_sport_registry_source_906]]):
-- coverage copy across the app no longer distinguishes live vs coming-soon.
-- Sport state stays in the registry for internal use (fetch cadences,
-- data availability) but user-facing state_message/today_note are nulled
-- unless there's something operationally worth surfacing (weather delay,
-- data outage, etc.).
--
-- What this migration does:
--   - NFL:   state → in_season, clear preseason messages
--   - NCAAF: state → in_season, clear preseason messages
--   - Other sports untouched.

UPDATE public.sport_registry SET
  state = 'in_season',
  today_note = NULL,
  tomorrow_note = NULL,
  state_message = NULL,
  return_date = NULL
WHERE sport = 'NFL';

UPDATE public.sport_registry SET
  state = 'in_season',
  today_note = NULL,
  tomorrow_note = NULL,
  state_message = NULL,
  return_date = NULL
WHERE sport = 'NCAAF';

-- Also clear any lingering nfl_game_context rows still tagged
-- stats_source='preseason' but with commence_time in the regular season
-- (>= 2026-09-09 = TNF opener). Next NFL pipeline run will re-populate
-- these rows with the correct stats_source ('current' or
-- 'prior_season_regressed'). Nulling to 'current' as a safe default
-- so the app doesn't render the (already-killed) preseason badge in
-- the interim, and so downstream skip-rules that gate on preseason
-- stop firing on regular-season games.
UPDATE public.nfl_game_context
   SET stats_source = 'current'
 WHERE stats_source = 'preseason'
   AND commence_time >= '2026-09-09'::timestamptz;

-- Force PostgREST schema reload so the app picks up the new values on next
-- fetch (no app resubmit needed).
NOTIFY pgrst, 'reload schema';
