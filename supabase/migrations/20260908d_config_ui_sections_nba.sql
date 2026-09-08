-- 20260908d — config_ui_sections seed for NBA sport-specific cards
-- wrapped in the 4th batch of wraps 9/8. NCAAB rows already exist from
-- 20260908a. NBA needs injuries + team_snapshot rows.

INSERT INTO public.config_ui_sections (sport, surface, section_key, enabled, reason) VALUES
  ('NBA', 'game_detail', 'injuries', true, 'OUT/DOUBTFUL/QUESTIONABLE NBA feed')
ON CONFLICT (sport, surface, section_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
