-- 20260908b — config_ui_sections seed additions for the second batch of
-- football (NFL + NCAAF) card wraps shipped 9/8. Migration is additive
-- and idempotent (ON CONFLICT DO NOTHING) so re-running is safe.

INSERT INTO public.config_ui_sections (sport, surface, section_key, enabled, reason) VALUES
  ('NFL', 'game_detail', 'situational', true,  'chip row: divisional, rest gap, cohort tags'),
  ('NFL', 'game_detail', 'injuries',    true,  'Out/Doubtful/Questionable NFL injury feed'),
  ('NFL', 'game_detail', 'qb_matchup',  true,  'starter QB comparison + weekly projections')
ON CONFLICT (sport, surface, section_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
