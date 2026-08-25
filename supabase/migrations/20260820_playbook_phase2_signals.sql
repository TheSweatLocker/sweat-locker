-- 2026-08-20 Playbook Phase 2+3: wire remaining registry-graded signals
-- Only 4 signals were genuinely missing after accounting for alt-name
-- matches (batter_l7_hot ↔ prop:l7_hot etc):
--   prop:team_cold   — 62.8% hit, n=199  (VALIDATED)
--   prop:clean_start — 56.8% hit, n=176  (VALIDATED)
--   prop:l7_cold     — 55.0% hit, n=120  (VALIDATED)
--   prop:park        — 53.9% hit, n=1689 (DISCOVERY, huge sample)
--
-- Same pattern as Phase 1: legacy already computes + stores in
-- signals field; playbook now sees them via signal_sources rows.
-- direction_hint=FOLLOW in registry → legacy attaches signal to prop
-- when it supports the pick → BACK from playbook's POV.

BEGIN;

-- Same unique-constraint guard as Phase 1 (idempotent — safe if Phase 1
-- ran first or if this runs alone).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'signal_sources_key_sport_uk'
  ) THEN
    ALTER TABLE signal_sources
      ADD CONSTRAINT signal_sources_key_sport_uk UNIQUE (signal_key, sport);
  END IF;
END $$;

INSERT INTO signal_sources (
  signal_key, sport, class, market_scope,
  condition_expr, side_expr, strength_expr,
  weight_registry_key, display_prose_template, description,
  enabled, origin, subject_scope
) VALUES

('prop_team_cold', 'MLB', 'prop_form', 'hits',
 $$p.get('signals', {}).get('team_cold') is not None$$,
 $$'BACK'$$,
 $$0.8$$,
 'prop:team_cold',
 'team cold — {signals[team_cold]}',
 'Team L10 R/G ≤ -0.5 vs season baseline — team offense cooling. 62.8% hit on n=199 (VALIDATED). Supports hits_under.',
 true, 'SEEDED_PHASE2_2026-08-20', 'prop'),

('prop_clean_start', 'MLB', 'prop_trend', 'pitcher',
 $$p.get('signals', {}).get('clean_start') is not None$$,
 $$'BACK'$$,
 $$0.65$$,
 'prop:clean_start',
 'pitcher clean 1st inning — {signals[clean_start]}',
 'Pitcher 1st-inn WHIP ≤ 1.10 — attacks the zone early, low BB probability. 56.8% hit on n=176 (VALIDATED).',
 true, 'SEEDED_PHASE2_2026-08-20', 'prop'),

('prop_l7_cold', 'MLB', 'prop_trend', 'hits',
 $$p.get('signals', {}).get('l7_cold') is not None$$,
 $$'BACK'$$,
 $$0.6$$,
 'prop:l7_cold',
 'batter L7 cold — {signals[l7_cold]}',
 'Batter L7 got-hit rate ≤ 40% — real recent slump. 55.0% hit on n=120 (VALIDATED). Supports hits_under.',
 true, 'SEEDED_PHASE2_2026-08-20', 'prop'),

('prop_park', 'MLB', 'prop_environment', '*',
 $$p.get('signals', {}).get('park') is not None$$,
 $$'BACK'$$,
 $$0.55$$,
 'prop:park',
 'park context — {signals[park]}',
 'Park factor context — legacy scorer flagged as material for this prop type. 53.9% hit on huge n=1689 (DISCOVERY). Small edge but very consistent.',
 true, 'SEEDED_PHASE2_2026-08-20', 'prop')

ON CONFLICT (signal_key, sport) DO UPDATE SET
  class = EXCLUDED.class,
  market_scope = EXCLUDED.market_scope,
  condition_expr = EXCLUDED.condition_expr,
  side_expr = EXCLUDED.side_expr,
  strength_expr = EXCLUDED.strength_expr,
  weight_registry_key = EXCLUDED.weight_registry_key,
  display_prose_template = EXCLUDED.display_prose_template,
  description = EXCLUDED.description,
  enabled = EXCLUDED.enabled,
  updated_at = NOW();

COMMIT;

NOTIFY pgrst, 'reload schema';
