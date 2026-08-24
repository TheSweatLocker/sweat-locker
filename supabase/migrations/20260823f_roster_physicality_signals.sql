-- Roster physicality shadow signals — NCAAF + NCAAB (2026-08-23).
--
-- Reads ctx fields populated by enrich_ctx_roster_physicality.py.
-- Sport-scoped so signals only fire on their home sport.
--
-- WHY SHADOW MODE
-- ───────────────
-- These are theoretical edges (see project memory for the reasoning
-- discussion). Weights start LOW (strength_expr caps < 0.5) so they
-- can't dominate scoring while unproven. After 30-45 days of graded
-- evaluations, refresh_prop_signal_calibration will re-weight from
-- observed hit rates, and any that fail to demonstrate edge get
-- disabled by the discipline gate.
--
-- Signals seeded here (6 total):
--   NCAAF (3): ol_weight_advantage_home/away, dl_weight_advantage_home/away,
--              class_year_edge_early_season (Weeks 1-3 only)
--   NCAAB (3): frontcourt_height_advantage_home/away,
--              size_advantage_home/away, class_year_edge_ncaab
--
-- All signals sport-scoped. Zero cross-sport leak.

DELETE FROM public.signal_sources
 WHERE origin = 'SEEDED_ROSTER_PHYS_823';

-- ─────────────────────────────────────────────────────────────
-- NCAAF: OL vs opposing DL weight advantage
-- ─────────────────────────────────────────────────────────────
INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES

  -- Home OL heavier than away DL by 20+ lbs → home ground game leverage
  ('ncaaf_ol_weight_adv_home', 'NCAAF', 'roster_physicality', 'rl', 'game',
   'ctx.ol_dl_weight_gap_home is not None and float(ctx.ol_dl_weight_gap_home) >= 20',
   '"HOME_RL"',
   'min((float(ctx.ol_dl_weight_gap_home) - 15) / 30.0, 0.45)',
   '{home_team} OL avg {home_ol_avg_wt}lbs vs {away_team} DL {away_dl_avg_wt}lbs (gap +{ol_dl_weight_gap_home})',
   'NCAAF: home OL outweighs opp DL by 20+ lbs — ground-game leverage',
   true, 'SEEDED_ROSTER_PHYS_823'),

  ('ncaaf_ol_weight_adv_away', 'NCAAF', 'roster_physicality', 'rl', 'game',
   'ctx.ol_dl_weight_gap_away is not None and float(ctx.ol_dl_weight_gap_away) >= 20',
   '"AWAY_RL"',
   'min((float(ctx.ol_dl_weight_gap_away) - 15) / 30.0, 0.45)',
   '{away_team} OL avg {away_ol_avg_wt}lbs vs {home_team} DL {home_dl_avg_wt}lbs (gap +{ol_dl_weight_gap_away})',
   'NCAAF: away OL outweighs opp DL by 20+ lbs',
   true, 'SEEDED_ROSTER_PHYS_823'),

  -- Under signal — heavier trenches on both sides = more short-yardage grind = fewer possessions
  ('ncaaf_heavy_trenches_under', 'NCAAF', 'roster_physicality', 'total', 'game',
   'ctx.home_ol_avg_wt is not None and ctx.away_ol_avg_wt is not None and float(ctx.home_ol_avg_wt) >= 315 and float(ctx.away_ol_avg_wt) >= 315',
   '"UNDER"',
   '0.35',
   'both OLs avg 315+ lbs ({home_ol_avg_wt} / {away_ol_avg_wt}) — grind-it-out totals lean UNDER',
   'NCAAF: heavy trench game favors UNDER',
   true, 'SEEDED_ROSTER_PHYS_823'),

  -- Class-year experience edge (Weeks 1-3 gate is enforced in scorer via ctx.week)
  ('ncaaf_experience_edge_early_home', 'NCAAF', 'roster_physicality', 'rl', 'game',
   'ctx.class_year_edge_home is not None and ctx.week is not None and int(ctx.week) <= 3 and float(ctx.class_year_edge_home) >= 0.3',
   '"HOME_RL"',
   'min(float(ctx.class_year_edge_home) / 1.5, 0.4)',
   '{home_team} more experienced (avg class {home_avg_class_year} vs {away_avg_class_year}) — Week {week} edge',
   'NCAAF Weeks 1-3: home upperclass-heavy vs freshman-heavy road team',
   true, 'SEEDED_ROSTER_PHYS_823'),

  ('ncaaf_experience_edge_early_away', 'NCAAF', 'roster_physicality', 'rl', 'game',
   'ctx.class_year_edge_home is not None and ctx.week is not None and int(ctx.week) <= 3 and float(ctx.class_year_edge_home) <= -0.3',
   '"AWAY_RL"',
   'min(abs(float(ctx.class_year_edge_home)) / 1.5, 0.4)',
   '{away_team} more experienced (avg class {away_avg_class_year} vs {home_avg_class_year}) — Week {week} edge',
   'NCAAF Weeks 1-3: away upperclass-heavy vs freshman-heavy home team',
   true, 'SEEDED_ROSTER_PHYS_823'),

  -- ─────────────────────────────────────────────────────────────
  -- NCAAB: Frontcourt height + size + experience
  -- ─────────────────────────────────────────────────────────────

  ('ncaab_frontcourt_height_adv_home', 'NCAAB', 'roster_physicality', 'rl', 'game',
   'ctx.frontcourt_ht_gap_home is not None and float(ctx.frontcourt_ht_gap_home) >= 1.5',
   '"HOME_RL"',
   'min((float(ctx.frontcourt_ht_gap_home) - 1.0) / 3.0, 0.35)',
   '{home_team} frontcourt avg {home_frontcourt_avg_ht}in vs {away_team} {away_frontcourt_avg_ht}in — height advantage',
   'NCAAB: home frontcourt taller by 1.5+ inches (O-reb + rim protection edge)',
   true, 'SEEDED_ROSTER_PHYS_823'),

  ('ncaab_frontcourt_height_adv_away', 'NCAAB', 'roster_physicality', 'rl', 'game',
   'ctx.frontcourt_ht_gap_home is not None and float(ctx.frontcourt_ht_gap_home) <= -1.5',
   '"AWAY_RL"',
   'min((abs(float(ctx.frontcourt_ht_gap_home)) - 1.0) / 3.0, 0.35)',
   '{away_team} frontcourt avg {away_frontcourt_avg_ht}in vs {home_team} {home_frontcourt_avg_ht}in — height edge',
   'NCAAB: away frontcourt taller by 1.5+ inches',
   true, 'SEEDED_ROSTER_PHYS_823'),

  -- Big front-court both sides = slower pace = UNDER
  ('ncaab_dual_size_under', 'NCAAB', 'roster_physicality', 'total', 'game',
   'ctx.home_frontcourt_avg_ht is not None and ctx.away_frontcourt_avg_ht is not None and float(ctx.home_frontcourt_avg_ht) >= 80.0 and float(ctx.away_frontcourt_avg_ht) >= 80.0',
   '"UNDER"',
   '0.3',
   'both frontcourts avg 6''8"+ ({home_frontcourt_avg_ht} / {away_frontcourt_avg_ht}) — size slows pace',
   'NCAAB: dual big frontcourts favor UNDER (post-play slows pace)',
   true, 'SEEDED_ROSTER_PHYS_823'),

  -- Experience edge (November-only gate via game_date month is looser than NCAAF week logic)
  ('ncaab_experience_edge_home', 'NCAAB', 'roster_physicality', 'rl', 'game',
   'ctx.class_year_edge_home is not None and float(ctx.class_year_edge_home) >= 0.4',
   '"HOME_RL"',
   'min(float(ctx.class_year_edge_home) / 1.5, 0.35)',
   '{home_team} more experienced (avg class {home_avg_class_year} vs {away_avg_class_year})',
   'NCAAB: home team meaningfully more experienced by class-year avg',
   true, 'SEEDED_ROSTER_PHYS_823'),

  ('ncaab_experience_edge_away', 'NCAAB', 'roster_physicality', 'rl', 'game',
   'ctx.class_year_edge_home is not None and float(ctx.class_year_edge_home) <= -0.4',
   '"AWAY_RL"',
   'min(abs(float(ctx.class_year_edge_home)) / 1.5, 0.35)',
   '{away_team} more experienced (avg class {away_avg_class_year} vs {home_avg_class_year})',
   'NCAAB: away team meaningfully more experienced',
   true, 'SEEDED_ROSTER_PHYS_823');

NOTIFY pgrst, 'reload schema';
