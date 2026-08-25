-- Roster Talent name scrub (2026-08-25).
--
-- Per [[feedback_tos_scrub_source_names]] + user directive during NFL slot
-- redesign (2026-08-25): no provider names in user-facing copy. The Madden
-- Top-100 signals shipped 2026-08-24 leak "Madden" into signal_registry
-- description + category — both are surfaced in the app.
--
-- Backend column names (home_madden_ovr, madden_ovr_gap_home, etc) stay as
-- they are — those are internal only. The scrub is limited to text fields
-- that render in the UI (description, category, direction_hint copy).
--
-- What renders where:
--   - signal_registry.description → external picks explainer + jerry-cited
--     signals in sweat card headline
--   - signal_registry.category    → grouping label above the lens confluence
--
-- Idempotent.

UPDATE public.signal_registry
   SET description = REGEXP_REPLACE(description, 'Madden(?: NFL 27| roster| OVR| ratings?)?',
                                    'roster-talent', 'gi'),
       category    = 'roster_talent'
 WHERE sport = 'NFL'
   AND (category = 'madden_talent'
        OR description ILIKE '%madden%');

-- signal_sources — 3 text fields to scrub:
--   description            → operator-facing but sometimes shown in prose
--   class                  → grouping label (rendered as category)
--   display_prose_template → THE Jerry-cited signal string in the app
--
-- Replacements:
--   "Madden OVR"    → "roster OVR"
--   "Madden roster" → "roster"
--   generic "Madden" → "roster-talent"
UPDATE public.signal_sources
   SET description            = REGEXP_REPLACE(description,
                                'Madden(?: NFL 27| roster| OVR| ratings?)?',
                                'roster-talent', 'gi'),
       class                  = 'roster_talent',
       display_prose_template = REGEXP_REPLACE(
                                REGEXP_REPLACE(display_prose_template,
                                  'Madden OVR', 'roster OVR', 'gi'),
                                'Madden(?: roster| ratings?)?',
                                'roster-talent', 'gi')
 WHERE sport = 'NFL'
   AND (class = 'madden_talent'
        OR description ILIKE '%madden%'
        OR display_prose_template ILIKE '%madden%');

NOTIFY pgrst, 'reload schema';
