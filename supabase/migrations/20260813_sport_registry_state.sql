-- Sport registry state extension (2026-08-13).
--
-- Adds fields the app reads to render per-sport UX without hardcodes:
-- season state, tab scope (daily vs weekly vs event-based), per-sport
-- today/tomorrow notes, and off-season return messaging.
--
-- Problem this solves: hardcoded 2-branch note in app/index.tsx (MLB vs
-- everything-else) had every non-MLB sport reading the same generic
-- "opening lines and early context" copy. NCAAB had a hardcoded empty
-- state. NFL preseason had no way to signal "lines only, no picks".
-- Off-season states (MLB after World Series, NBA in summer) had no
-- distinct treatment.
--
-- All of this now lives in one row per sport. Editing the row updates
-- the app without a store resubmission.
--
-- Fields:
--   state         — current state driving downstream behavior
--   state_message — banner text shown at top of games list per state
--   today_note    — top-of-Today tab note (null = no note rendered)
--   tomorrow_note — top-of-Tomorrow tab note (null = default)
--   tab_scope     — 'daily' (Today/Tomorrow), 'weekly' (This Week/Next Week),
--                   'event' (This Card/Next Card). Drives tab labels.
--   return_date   — when off_season/returning state ends. Powers countdown.
--   ladder_eligible — whether ladder engine considers plays from this sport.
--                     Default true; set false during preseason (no signal).

ALTER TABLE public.sport_registry
  ADD COLUMN IF NOT EXISTS state TEXT
    NOT NULL DEFAULT 'in_season'
    CHECK (state IN ('in_season','preseason','postseason','off_season','returning')),
  ADD COLUMN IF NOT EXISTS state_message TEXT,
  ADD COLUMN IF NOT EXISTS today_note TEXT,
  ADD COLUMN IF NOT EXISTS tomorrow_note TEXT,
  ADD COLUMN IF NOT EXISTS tab_scope TEXT
    NOT NULL DEFAULT 'daily'
    CHECK (tab_scope IN ('daily','weekly','event')),
  ADD COLUMN IF NOT EXISTS return_date DATE,
  ADD COLUMN IF NOT EXISTS ladder_eligible BOOLEAN NOT NULL DEFAULT true;

-- Seed the current state per sport as of 2026-08-13.
-- MLB: mid-August regular season. WS ends ~Nov 5. After that flip to off_season.
-- NFL: preseason today (Aug 7 kickoff already happened, Sept 4 regular season).
-- NCAAF: pre-season, opens Aug 22.
-- NCAAB: off_season until Nov 3.
-- NBA: off_season until Oct 21.
-- NHL: off_season until Oct 8.
-- UFC: event-based, always active if a card exists.

UPDATE public.sport_registry SET
  state = 'in_season',
  tab_scope = 'daily',
  today_note = NULL,
  tomorrow_note = 'Preview — probable pitchers, opening lines, weather, and stat projections refresh by 4pm ET today. Confirmed lineups, conviction tiers, and final picks land by 11am ET tomorrow.',
  state_message = NULL,
  return_date = NULL,
  ladder_eligible = true
WHERE sport = 'MLB';

UPDATE public.sport_registry SET
  state = 'preseason',
  tab_scope = 'weekly',
  today_note = 'Preseason · lines only, no picks. Full model coverage starts Week 1 (Sept 4).',
  tomorrow_note = 'Preseason preview — depth charts, injury reports, opening lines. Panel projections light up Week 1.',
  state_message = 'NFL preseason · lines only, no picks. Regular season kicks off Sept 4.',
  return_date = '2026-09-04',
  ladder_eligible = false
WHERE sport = 'NFL';

UPDATE public.sport_registry SET
  state = 'preseason',
  tab_scope = 'weekly',
  today_note = NULL,
  tomorrow_note = 'Preview — depth charts, weather, SP+ efficiency. Final picks by Friday night for Saturday slate.',
  state_message = 'NCAAF season kicks off Aug 22 · Week 0 preview',
  return_date = '2026-08-22',
  ladder_eligible = false
WHERE sport = 'NCAAF';

UPDATE public.sport_registry SET
  state = 'off_season',
  tab_scope = 'daily',
  today_note = NULL,
  tomorrow_note = NULL,
  state_message = 'NCAAB season starts Nov 3 — probable starters, KenPom refresh, and opening lines land the week before.',
  return_date = '2026-11-03',
  ladder_eligible = false
WHERE sport = 'NCAAB';

UPDATE public.sport_registry SET
  state = 'off_season',
  tab_scope = 'daily',
  today_note = NULL,
  tomorrow_note = NULL,
  state_message = 'NBA season starts Oct 21 — training camp signals and Panel projections land in early Oct.',
  return_date = '2026-10-21',
  ladder_eligible = false
WHERE sport = 'NBA';

UPDATE public.sport_registry SET
  state = 'off_season',
  tab_scope = 'daily',
  today_note = NULL,
  tomorrow_note = NULL,
  state_message = 'NHL season starts Oct 8 — probable goalies and MoneyPuck refresh land end of September.',
  return_date = '2026-10-08',
  ladder_eligible = false
WHERE sport = 'NHL';

UPDATE public.sport_registry SET
  state = 'in_season',
  tab_scope = 'event',
  today_note = NULL,
  tomorrow_note = 'Card locked · odds refresh nightly. Full picks land 24 hrs before main card.',
  state_message = NULL,
  return_date = NULL,
  ladder_eligible = true
WHERE sport = 'UFC';

-- Once World Series ends (est 2026-11-05), an operator runs:
--   UPDATE sport_registry SET state='off_season',
--     state_message='MLB season complete. See you in Spring Training 2027.',
--     return_date='2027-03-27', ladder_eligible=false WHERE sport='MLB';
--
-- No app resubmission required.

NOTIFY pgrst, 'reload schema';
