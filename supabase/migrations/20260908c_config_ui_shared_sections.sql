-- 20260908c — config_ui_sections seed for shared universal Game Detail
-- sections wrapped in the 3rd batch of wraps 9/8. These are the sections
-- rendered by the top-level GameDetailV2 component (not sport-specific
-- cards). Adding sport='ALL' rows so they toggle across every sport by
-- default; sport-specific rows can override.
--
-- All the corresponding section_keys already exist from 20260908a as
-- 'ALL' seed rows; this is just belt-and-suspenders / documentation.

INSERT INTO public.config_ui_sections (sport, surface, section_key, enabled, reason) VALUES
  ('ALL', 'game_detail', 'money_flow',            true, 'shared: bets vs money · sharps vs public'),
  ('ALL', 'game_detail', 'line_movement',         true, 'shared: opening → current line drift'),
  ('ALL', 'game_detail', 'model_consensus',       true, 'shared: model panel agreement chart'),
  ('ALL', 'game_detail', 'external_handicappers', true, 'shared: per-source aggregated handicapper picks'),
  ('ALL', 'game_detail', 'recent_schedule',       true, 'shared: last 5 games ATS + O/U rollup'),
  ('ALL', 'game_detail', 'situational_records',   true, 'shared: record buckets × market'),
  ('ALL', 'game_detail', 'team_stats',            true, 'shared: raw value + rank per stat')
ON CONFLICT (sport, surface, section_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
