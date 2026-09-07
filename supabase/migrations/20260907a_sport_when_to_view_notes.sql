-- 2026-09-07: sport-aware "when to view analysis" hints for weekly sports.
-- Rendered via the today_note band on the Games tab (now styled gold per
-- 9/7 admin-note styling fix). Framing per user directive: NOT phrased as
-- "picks drop at X" (too tout-y). Descriptive of when the FULL analysis
-- is ready so users don't open early-week thinking product is broken.
--
-- Daily sports (MLB / NBA / NHL / NCAAB) don't need the hint since
-- analysis refreshes every morning — user opens, sees today's card, done.
--
-- Weekly sports (NFL / NCAAF) DO need it because analysis rolls in over
-- 24-48h ahead of kickoff. A Tuesday open with sparse Week-N-not-yet-
-- generated cards could read as broken.
--
-- UFC is technically event-based but has weekly cadence — analysis lands
-- Wednesday of fight week.
--
-- These notes are DB-driven so ops can update them without an app resubmit.
-- Kill the note by setting today_note = NULL (renders nothing). Change
-- copy by UPDATE ... SET today_note = 'new text'.

UPDATE public.sport_registry
   SET today_note = 'Full analysis populates by Thursday morning ahead of each week''s slate. Cards you see mid-week may be updated as injuries + line moves come in.'
 WHERE sport = 'NFL';

UPDATE public.sport_registry
   SET today_note = 'Full analysis populates by Wednesday for Thursday-Saturday slates. Cards you see earlier may be updated as opening lines settle.'
 WHERE sport = 'NCAAF';

-- Optional — UFC. Event-based cadence: card lands Wed of fight week.
UPDATE public.sport_registry
   SET today_note = 'Full fight-card analysis populates Wednesday of fight week. Adds weight-cut + layoff signals after Friday weigh-ins.'
 WHERE sport = 'UFC';

-- Force PostgREST schema reload so the app picks up the new values on
-- next fetch (no app resubmit needed).
NOTIFY pgrst, 'reload schema';
