-- 20260908e — config_ui_sections seed for the final 4 shared sections
-- wrapped 9/8. Completes v1.0.1 render-manifest coverage (26 of 29
-- unique section_keys now controllable via config; remaining 3 are
-- variants that share keys with their parents, e.g., NCAAFRostersRichCard
-- and NCAAFRostersCard both use 'rosters_continuity').

INSERT INTO public.config_ui_sections (sport, surface, section_key, enabled, reason) VALUES
  ('ALL', 'game_detail', 'sportsbook_odds', true, 'per-book lines + parlay/log-pick actions')
  -- market, predicted_score, stat_projections already seeded in 20260908a
ON CONFLICT (sport, surface, section_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
