-- Prop playbook projection-edge signals (2026-08-17).
--
-- Surfaces the model projection edge as first-class playbook signals.
-- Legacy scoring uses _edge_at_book internally but doesn't chip it.
-- Playbook makes it explicit + tracked.
--
-- Convention (verified 2026-08-17):
--   _edge_at_book > 0 → projection supports the prop row's direction
--   _edge_at_book < 0 → projection opposes the prop row's direction
--
-- Depends on: 20260817_prop_playbook_infra.sql
--
-- Three signals — cover the spectrum of projection agreement:
--   1. projection_edge_supports  — meaningful edge in the direction (>=0.2)
--   2. projection_edge_strong    — strong edge in direction (>=0.7)
--   3. projection_edge_opposes   — projection SAYS the other way (<= -0.3)
--
-- Class = 'prop_model' (new class — signals from model projections
-- distinct from _form/_trend/_matchup/_environment).

DELETE FROM public.signal_sources
 WHERE sport IN ('MLB', 'NFL')
   AND class = 'prop_model'
   AND origin = 'SEEDED_PROJ_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  ('projection_edge_supports', 'MLB', 'prop_model', '*', 'prop',
   'isinstance(p.get("signals"), dict) and "_edge_at_book" in p["signals"] and p["signals"]["_edge_at_book"] is not None and float(p["signals"]["_edge_at_book"]) >= 0.2',
   '"BACK"',
   'min(float(p["signals"]["_edge_at_book"]) / 1.0, 1.0)',
   'model projection supports direction — {_edge_at_book} edge at book line',
   'Model projection is meaningfully aligned with the prop direction (edge >= 0.2)',
   true, 'SEEDED_PROJ_817'),

  ('projection_edge_strong', 'MLB', 'prop_model', '*', 'prop',
   'isinstance(p.get("signals"), dict) and "_edge_at_book" in p["signals"] and p["signals"]["_edge_at_book"] is not None and float(p["signals"]["_edge_at_book"]) >= 0.7',
   '"BACK"',
   'min(float(p["signals"]["_edge_at_book"]) / 1.5, 1.0)',
   'model projection STRONG edge — {_edge_at_book} at book line',
   'Model projection is well beyond noise threshold vs book line (edge >= 0.7). Stacks with edge_supports.',
   true, 'SEEDED_PROJ_817'),

  ('projection_edge_opposes', 'MLB', 'prop_model', '*', 'prop',
   'isinstance(p.get("signals"), dict) and "_edge_at_book" in p["signals"] and p["signals"]["_edge_at_book"] is not None and float(p["signals"]["_edge_at_book"]) <= -0.3',
   '"FADE"',
   'min(abs(float(p["signals"]["_edge_at_book"])) / 1.0, 1.0)',
   'model projection OPPOSES direction — {_edge_at_book} against pick',
   'Model projection lands on the OTHER side of the book line. Fires FADE.',
   true, 'SEEDED_PROJ_817'),

  -- NFL variants (same convention, applies when NFL prop pipeline seeds
  -- _edge_at_book on nfl_pipeline_props)
  ('projection_edge_supports', 'NFL', 'prop_model', '*', 'prop',
   'isinstance(p.get("signals"), dict) and "_edge_at_book" in p["signals"] and p["signals"]["_edge_at_book"] is not None and float(p["signals"]["_edge_at_book"]) >= 0.2',
   '"BACK"',
   'min(float(p["signals"]["_edge_at_book"]) / 1.0, 1.0)',
   'model projection supports direction — {_edge_at_book} edge at book line',
   'NFL variant of MLB projection_edge_supports',
   true, 'SEEDED_PROJ_817'),

  ('projection_edge_strong', 'NFL', 'prop_model', '*', 'prop',
   'isinstance(p.get("signals"), dict) and "_edge_at_book" in p["signals"] and p["signals"]["_edge_at_book"] is not None and float(p["signals"]["_edge_at_book"]) >= 0.7',
   '"BACK"',
   'min(float(p["signals"]["_edge_at_book"]) / 1.5, 1.0)',
   'model projection STRONG edge — {_edge_at_book} at book line',
   'NFL variant of MLB projection_edge_strong',
   true, 'SEEDED_PROJ_817');

NOTIFY pgrst, 'reload schema';
