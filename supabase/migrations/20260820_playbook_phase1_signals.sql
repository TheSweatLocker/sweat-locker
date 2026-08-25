-- 2026-08-20 Playbook Phase 1: wire 5 highest-value missing signals
-- into signal_sources so the playbook (prop_ensemble_scorer) can see
-- them. Legacy already fires these and stores them in signals; we just
-- need signal_sources rows so the playbook evaluates + weights them.
--
-- Registry entries and hit rates (all VALIDATED, MLB, from signal_registry
-- as of 2026-08-19 audit — see project_playbook_signal_gap_819):
--   prop:wind          — 76.3% hit, n=97
--   prop:team_heat     — 68.4% hit, n=730
--   prop:team_offense  — 66.2% hit, n=657
--   prop:l7_avg_hot    — 64.5% hit, n=504
--   prop:short_last    — 65.4% hit, n=153
--
-- Strategy: instead of re-implementing each condition against ctx, we
-- key off the legacy-populated `signals` field on the prop row. Legacy
-- already applied the logic (temp/wind thresholds, wRC+ tiers, etc);
-- the presence of the key IS the condition. Side is BACK for all five
-- (legacy fires these to support the prop direction). Strength varies
-- by signal potency.

BEGIN;

-- Ensure the natural key is unique so the UPSERT works. If a duplicate
-- already exists we surface a friendly error instead of silently letting
-- the constraint fail below. Wrapped IF NOT EXISTS since we may re-run.
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

('prop_wind', 'MLB', 'prop_environment', 'hits',
 $$p.get('signals', {}).get('wind') is not None$$,
 $$'BACK'$$,
 $$0.7$$,
 'prop:wind',
 'wind favorable — {signals[wind]}',
 'Wind ≥10mph blowing out (S/SW/SE) — legacy scorer flagged. 76.3% hit on n=97 (VALIDATED).',
 true, 'SEEDED_PHASE1_2026-08-20', 'prop'),

('prop_team_heat', 'MLB', 'prop_form', '*',
 $$p.get('signals', {}).get('team_heat') is not None$$,
 $$'BACK'$$,
 $$0.85$$,
 'prop:team_heat',
 'team heat — {signals[team_heat]}',
 'Team L10 R/G ≥ +0.5 vs season baseline — team offense trending. 68.4% hit on n=730 (VALIDATED, biggest sample in registry).',
 true, 'SEEDED_PHASE1_2026-08-20', 'prop'),

('prop_team_offense', 'MLB', 'prop_form', '*',
 $$p.get('signals', {}).get('team_offense') is not None$$,
 $$'BACK'$$,
 $$0.8$$,
 'prop:team_offense',
 'team offense — {signals[team_offense]}',
 'Team wRC+ ≥ 105 vs opposing hand — favorable team-level context. 66.2% hit on n=657 (VALIDATED).',
 true, 'SEEDED_PHASE1_2026-08-20', 'prop'),

('prop_l7_avg_hot', 'MLB', 'prop_trend', 'hits',
 $$p.get('signals', {}).get('l7_avg_hot') is not None$$,
 $$'BACK'$$,
 $$0.75$$,
 'prop:l7_avg_hot',
 'batter L7 BA hot — {signals[l7_avg_hot]}',
 'Batter L7 BA ≥ .350 — real recent contact quality. 64.5% hit on n=504 (VALIDATED).',
 true, 'SEEDED_PHASE1_2026-08-20', 'prop'),

('prop_short_last', 'MLB', 'prop_form', 'pitcher',
 $$p.get('signals', {}).get('short_last') is not None$$,
 $$'BACK'$$,
 $$0.75$$,
 'prop:short_last',
 'pitcher fragile last outing — {signals[short_last]}',
 'Pitcher last start ≤ 4.5 IP — fragile / short leash risk. Signals bb/er/ha overs and outs-under. 65.4% hit on n=153 (VALIDATED).',
 true, 'SEEDED_PHASE1_2026-08-20', 'prop')

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
