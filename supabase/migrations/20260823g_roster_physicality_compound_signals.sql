-- Roster physicality COMPOUND signals — NCAAF + NCAAB (2026-08-23).
--
-- Phase 2 of the roster physicality stack. Where 20260823f seeded 9
-- standalone signals reading pure roster metrics, THIS migration adds
-- 8 compound signals that combine physicality with existing performance
-- data (EPA for NCAAF, KenPom four-factors for NCAAB).
--
-- WHY COMPOUND
-- ────────────
-- Physicality alone is a weak signal. "Home OL avg 320 lbs" doesn't
-- mean anything if the home team's rush offense is 130th in EPA and
-- the away team's rush defense is elite. But when physicality LINES UP
-- with performance data (heavy OL + strong home rush EPA + weak away
-- rush D), that stack is where the actual edge lives.
--
-- These fire at HIGHER strength (0.4-0.55) than the standalone
-- shadow signals because the combined condition is much more
-- restrictive — false-positive rate is lower.
--
-- SIGN CONVENTIONS
-- ────────────────
-- NCAAF def_epa_pp: HIGHER = worse defense (CFBD PPA convention)
--   - elite: <= -0.10, weak: >= 0.05 (conservative floor)
-- NCAAB adj_de: LOWER = better defense (KenPom points-allowed/100)
--   - elite: <= 95, weak: >= 105
-- NCAAB or_o: offensive rebound %, HIGHER = better
-- NCAAB pace_avg: possessions/game, LOWER = slower
--
-- All signals sport-scoped. Zero cross-sport leak.

DELETE FROM public.signal_sources
 WHERE origin = 'SEEDED_ROSTER_PHYS_COMPOUND_823';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES

  -- ─────────────────────────────────────────────────────────────
  -- NCAAF: OL weight + opposing rush-D weakness = HOME_RL
  -- ─────────────────────────────────────────────────────────────

  -- Home OL heavier by 20+ AND away defense giving up above-avg EPA/play
  ('ncaaf_ground_leverage_home', 'NCAAF', 'roster_physicality_compound', 'rl', 'game',
   'ctx.ol_dl_weight_gap_home is not None and float(ctx.ol_dl_weight_gap_home) >= 20 and ctx.away_def_epa_pp is not None and float(ctx.away_def_epa_pp) >= 0.05',
   '"HOME_RL"',
   'min(0.40 + (float(ctx.ol_dl_weight_gap_home) - 20) / 60.0 + max(0, float(ctx.away_def_epa_pp)) * 0.5, 0.55)',
   '{home_team} OL +{ol_dl_weight_gap_home}lbs vs {away_team} DL and away D allowing {away_def_epa_pp} EPA/play — ground leverage stacks',
   'NCAAF: home OL weight advantage + weak away defense — ground game leverage',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823'),

  ('ncaaf_ground_leverage_away', 'NCAAF', 'roster_physicality_compound', 'rl', 'game',
   'ctx.ol_dl_weight_gap_away is not None and float(ctx.ol_dl_weight_gap_away) >= 20 and ctx.home_def_epa_pp is not None and float(ctx.home_def_epa_pp) >= 0.05',
   '"AWAY_RL"',
   'min(0.40 + (float(ctx.ol_dl_weight_gap_away) - 20) / 60.0 + max(0, float(ctx.home_def_epa_pp)) * 0.5, 0.55)',
   '{away_team} OL +{ol_dl_weight_gap_away}lbs vs {home_team} DL and home D allowing {home_def_epa_pp} EPA/play',
   'NCAAF: away OL weight advantage + weak home defense',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823'),

  -- Defensive slugfest: both DLs heavy AND both defenses elite → UNDER
  -- Heavy DL alone doesn't mean UNDER (could force pass-heavy = OVER),
  -- but heavy DL + demonstrated elite D EPA = grind-it-out game
  ('ncaaf_defensive_slugfest_under', 'NCAAF', 'roster_physicality_compound', 'total', 'game',
   'ctx.home_dl_avg_wt is not None and ctx.away_dl_avg_wt is not None and float(ctx.home_dl_avg_wt) >= 285 and float(ctx.away_dl_avg_wt) >= 285 and ctx.home_def_epa_pp is not None and ctx.away_def_epa_pp is not None and float(ctx.home_def_epa_pp) <= -0.05 and float(ctx.away_def_epa_pp) <= -0.05',
   '"UNDER"',
   '0.50',
   'both DLs 285+ lbs ({home_dl_avg_wt} / {away_dl_avg_wt}) + both Ds allowing <= -0.05 EPA/play — defensive slugfest',
   'NCAAF: heavy DLs on both sides + elite defensive EPA — UNDER stack',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823'),

  -- Experienced road team (senior-heavy) as small underdog Weeks 1-3
  -- (Weeks 1-3 gate protects against noise as season stabilizes)
  ('ncaaf_experienced_road_dog_early', 'NCAAF', 'roster_physicality_compound', 'rl', 'game',
   'ctx.away_avg_class_year is not None and float(ctx.away_avg_class_year) >= 3.2 and ctx.close_spread is not None and abs(float(ctx.close_spread)) <= 7 and ctx.close_spread is not None and float(ctx.close_spread) < 0 and ctx.week is not None and int(ctx.week) <= 3',
   '"AWAY_RL"',
   '0.40',
   '{away_team} senior-heavy (avg class {away_avg_class_year}) as road dog <=7 in Week {week} — experience covers early',
   'NCAAF Weeks 1-3: experienced (class 3.2+) road small dog covers',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823'),

  -- ─────────────────────────────────────────────────────────────
  -- NCAAB: size + defense + tempo compound stacks
  -- ─────────────────────────────────────────────────────────────

  -- Big frontcourts on both sides + both defenses elite → UNDER
  ('ncaab_size_defense_stack_under', 'NCAAB', 'roster_physicality_compound', 'total', 'game',
   'ctx.home_frontcourt_avg_ht is not None and ctx.away_frontcourt_avg_ht is not None and float(ctx.home_frontcourt_avg_ht) >= 80.0 and float(ctx.away_frontcourt_avg_ht) >= 80.0 and ctx.home_adj_de is not None and ctx.away_adj_de is not None and float(ctx.home_adj_de) <= 100 and float(ctx.away_adj_de) <= 100',
   '"UNDER"',
   '0.50',
   'both FCs 6''8"+ ({home_frontcourt_avg_ht} / {away_frontcourt_avg_ht}) + both Ds top-tier (KenPom adj_de {home_adj_de} / {away_adj_de}) — size + D locks the paint',
   'NCAAB: dual big frontcourts + dual elite defense — high-conviction UNDER',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823'),

  -- Slow-pace teams with big frontcourts → UNDER (double convergence)
  ('ncaab_size_slow_pace_under', 'NCAAB', 'roster_physicality_compound', 'total', 'game',
   'ctx.pace_avg is not None and float(ctx.pace_avg) <= 66 and ctx.home_frontcourt_avg_ht is not None and ctx.away_frontcourt_avg_ht is not None and float(ctx.home_frontcourt_avg_ht) >= 79.0 and float(ctx.away_frontcourt_avg_ht) >= 79.0',
   '"UNDER"',
   '0.45',
   'slow pace ({pace_avg} poss/game) + dual big frontcourts — total lean UNDER',
   'NCAAB: slow pace + big FCs — pace × size UNDER stack',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823'),

  -- Experienced favorite at home + elite defense → RL cover
  ('ncaab_experienced_home_fav', 'NCAAB', 'roster_physicality_compound', 'rl', 'game',
   'ctx.class_year_edge_home is not None and float(ctx.class_year_edge_home) >= 0.4 and ctx.close_spread is not None and float(ctx.close_spread) <= -3 and ctx.home_adj_de is not None and float(ctx.home_adj_de) <= 100',
   '"HOME_RL"',
   '0.45',
   '{home_team} experienced (class edge +{class_year_edge_home}) + top-tier D (adj_de {home_adj_de}) as -{close_spread} fav',
   'NCAAB: experienced home fav with elite D — RL cover stack',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823'),

  -- Frontcourt height mismatch + strong opposing offensive rebounding = fade home
  -- Idea: undersized frontcourt gets abused on the boards by a good OREB team
  ('ncaab_undersized_home_or_fade', 'NCAAB', 'roster_physicality_compound', 'rl', 'game',
   'ctx.frontcourt_ht_gap_home is not None and float(ctx.frontcourt_ht_gap_home) <= -2.0 and ctx.away_or_o is not None and float(ctx.away_or_o) >= 30',
   '"AWAY_RL"',
   'min(0.35 + (abs(float(ctx.frontcourt_ht_gap_home)) - 2.0) / 5.0 + max(0, float(ctx.away_or_o) - 30) / 30.0, 0.50)',
   '{home_team} FC {home_frontcourt_avg_ht}in vs {away_team} FC {away_frontcourt_avg_ht}in ({frontcourt_ht_gap_home}in gap) + away OREB {away_or_o}% — size + boards edge',
   'NCAAB: home undersized frontcourt vs strong OREB away team — fade home',
   true, 'SEEDED_ROSTER_PHYS_COMPOUND_823');

NOTIFY pgrst, 'reload schema';
