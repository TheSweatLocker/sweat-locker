-- Seed the first 3 prop playbook signals (2026-08-17) — POC scope.
--
-- Depends on: 20260817_prop_playbook_infra.sql
--
-- These 3 signals PROVE the plug-in architecture works end-to-end
-- against shadow-mode. Once they fire cleanly for 3-5 days, we
-- expand the seed set (extract MLB legacy scoring branches into
-- individual signals per project_prop_playbook_design_817).
--
-- Signal 1: player_l10_vs_line_extreme (user's specific ask 8/17)
--   Player hit the prop line ≥8/10 or ≤2/10 last 10 = extreme trend
--   Applies to BOTH MLB + NFL props (subject_scope='player_prop')
--
-- Signal 2: refit_conviction_strong (surfaces existing refit as plug-in)
--   MLB refit_conviction ≥ 70 = load. NFL variant fires on legacy
--   conviction ≥ 70 since NFL refit isn't shipped.
--
-- Signal 3: hits_over_juice_trap (surfaces batter_hits_juice_trap_803)
--   MLB hits_over PRIME at -180+ juice = trap, FADE. Backtested
--   pattern from feedback memory.

DELETE FROM public.signal_sources
 WHERE subject_scope IN ('prop', 'player_prop')
   AND class = 'prop_form'
   AND origin = 'SEEDED';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  -- ── MLB signals ─────────────────────────────────────────────────
  ('player_l10_vs_line_extreme', 'MLB', 'prop_form', '*', 'player_prop',
   'p.get("player_l10_hit_count") is not None and (int(p["player_l10_hit_count"]) >= 8 or int(p["player_l10_hit_count"]) <= 2)',
   '"BACK" if (int(p["player_l10_hit_count"]) >= 8 and p["direction"] == "over") or (int(p["player_l10_hit_count"]) <= 2 and p["direction"] == "under") else "FADE"',
   'abs(int(p["player_l10_hit_count"]) - 5) / 5.0',
   '{player_name} hit this line in {player_l10_hit_count}/10 recent games — extreme trend',
   'Player L10 hit rate vs the exact prop line — extremes trend forward',
   true, 'SEEDED'),

  ('refit_conviction_strong', 'MLB', 'prop_form', '*', 'prop',
   'p.get("refit_conviction") is not None and float(p["refit_conviction"]) >= 70',
   '"BACK"',
   '(float(p["refit_conviction"]) - 50.0) / 50.0',
   'refit conviction {refit_conviction} — bucket-weighted signal load',
   'v2 refit weights say this bucket loads — surface as plug-in for playbook aggregation',
   true, 'SEEDED'),

  ('hits_over_juice_trap', 'MLB', 'prop_form', 'hits', 'prop',
   'p.get("prop_type") == "hits_over" and p.get("direction") == "over" and (p.get("tier") or "").upper() == "PRIME" and p.get("book_line") is not None and int(p["book_line"]) <= -180',
   '"FADE"',
   '0.6',
   'PRIME hits_over at heavy juice ({book_line}) — historical trap zone',
   'batter_hits_juice_trap_803 pattern — PRIME hits_over ~69% hit rate, juice pricing it in',
   true, 'SEEDED'),

  -- ── NFL signals ─────────────────────────────────────────────────
  ('player_l10_vs_line_extreme', 'NFL', 'prop_form', '*', 'player_prop',
   'p.get("player_l10_hit_count") is not None and (int(p["player_l10_hit_count"]) >= 8 or int(p["player_l10_hit_count"]) <= 2)',
   '"BACK" if (int(p["player_l10_hit_count"]) >= 8 and p["direction"] == "over") or (int(p["player_l10_hit_count"]) <= 2 and p["direction"] == "under") else "FADE"',
   'abs(int(p["player_l10_hit_count"]) - 5) / 5.0',
   '{player_name} hit this line in {player_l10_hit_count}/10 recent games — extreme trend',
   'Player L10 hit rate vs the exact prop line — extremes trend forward',
   true, 'SEEDED'),

  ('legacy_conviction_strong', 'NFL', 'prop_form', '*', 'prop',
   'p.get("conviction") is not None and float(p["conviction"]) >= 70',
   '"BACK"',
   '(float(p["conviction"]) - 50.0) / 50.0',
   'legacy conviction {conviction} — high-confidence scoring signal',
   'NFL has no refit yet; surface legacy conviction as playbook signal until refit ships',
   true, 'SEEDED');

NOTIFY pgrst, 'reload schema';
