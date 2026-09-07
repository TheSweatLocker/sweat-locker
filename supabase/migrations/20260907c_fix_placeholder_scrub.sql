-- 2026-09-07: fix broken placeholder tokens from 20260826c roster scrub.
--
-- The 8/26 scrub used REGEXP_REPLACE to turn "Madden" → "roster-talent" for
-- brand-hiding, but it also matched INSIDE the {placeholder} tokens in
-- display_prose_template — so column-name refs like {home_qb_madden_ovr}
-- became {home_qb_roster-talent_ovr}, which doesn't map to any nfl_game_context
-- column. Result: the formatter silently substitutes "?" wherever a
-- placeholder is unresolvable, and users see prose like:
--
--   "BAL QB roster-talent ? vs IND QB ? — QB edge on road"
--
-- (spot-checked by user on the BAL @ IND primary_play sub, 9/7)
--
-- Real DB columns on nfl_game_context (verified against live schema):
--   home_madden_ovr, away_madden_ovr
--   home_madden_off, away_madden_off
--   home_madden_def, away_madden_def
--   home_qb_madden_ovr, away_qb_madden_ovr
--   madden_ovr_gap_home, madden_off_gap_home, madden_off_gap_away, madden_qb_delta_home
--
-- Fix: reverse the over-scrub INSIDE {...} tokens only — the user-facing
-- prose text ("roster-talent", "roster OVR", "talent edge") stays scrubbed;
-- only the column-name refs go back to their real names.
--
-- Idempotent: repeated runs are no-ops after the first pass.

UPDATE public.signal_sources
   SET display_prose_template = REPLACE(
                                  REPLACE(
                                    REPLACE(
                                      REPLACE(display_prose_template,
                                        '{home_qb_roster-talent_ovr}', '{home_qb_madden_ovr}'),
                                      '{away_qb_roster-talent_ovr}',   '{away_qb_madden_ovr}'),
                                    '{home_roster-talent_ovr}',        '{home_madden_ovr}'),
                                  '{away_roster-talent_ovr}',          '{away_madden_ovr}')
 WHERE sport = 'NFL'
   AND display_prose_template LIKE '%{%roster-talent_%}%';

UPDATE public.signal_sources
   SET display_prose_template = REPLACE(
                                  REPLACE(display_prose_template,
                                    '{home_roster-talent_off}', '{home_madden_off}'),
                                  '{away_roster-talent_off}',   '{away_madden_off}')
 WHERE sport = 'NFL'
   AND display_prose_template LIKE '%{%roster-talent_off}%';

UPDATE public.signal_sources
   SET display_prose_template = REPLACE(
                                  REPLACE(display_prose_template,
                                    '{home_roster-talent_def}', '{home_madden_def}'),
                                  '{away_roster-talent_def}',   '{away_madden_def}')
 WHERE sport = 'NFL'
   AND display_prose_template LIKE '%{%roster-talent_def}%';

UPDATE public.signal_sources
   SET display_prose_template = REPLACE(
                                  REPLACE(
                                    REPLACE(display_prose_template,
                                      '{roster-talent_ovr_gap_home}', '{madden_ovr_gap_home}'),
                                    '{roster-talent_qb_delta_home}',  '{madden_qb_delta_home}'),
                                  '{roster-talent_off_gap_home}',     '{madden_off_gap_home}')
 WHERE sport = 'NFL'
   AND (display_prose_template LIKE '%{roster-talent_ovr_gap_%}%'
        OR display_prose_template LIKE '%{roster-talent_qb_delta_%}%'
        OR display_prose_template LIKE '%{roster-talent_off_gap_%}%');

NOTIFY pgrst, 'reload schema';
