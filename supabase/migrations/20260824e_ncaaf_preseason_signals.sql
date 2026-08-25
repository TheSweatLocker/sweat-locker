-- NCAAF pre-season signals (2026-08-24).
--
-- WHY THIS EXISTS
-- ───────────────
-- Week 1 (8/29) NCAAF cards are firing on only 2 signals per game (SP+
-- + cohort_home) because most signals in the ecosystem require IN-SEASON
-- game data (EPA, form, splits, recency) that doesn't exist yet.
--
-- User audit finding on TCU@UNC card: "only 2 models firing, card feels
-- bare." Correct behavior for a Week 0 signal ecosystem, but bad UX.
--
-- Fix: add signals that FIRE on data we DO have pre-season:
--   - SP+ magnitude tiers (huge favorite, mid favorite, small favorite)
--   - Physicality gaps (already shipped 8/23 as roster_physicality
--     signals but only fire on threshold hits — add wider-net variants)
--   - Home-field advantage baseline (fires on every NCAAF home team)
--   - Preseason big-gap talent flag (SP+ gap 15+ = talent mismatch)
--   - Class year experience early season (Weeks 1-3 gate)
--
-- These signals fire during Weeks 1-3 when EPA is thin, then get
-- naturally down-weighted by the ensemble as real-game signals stack.
-- All shadow-mode (strength cap 0.30-0.40) so they add breadth without
-- dominating. Reweight after 30d graded data via calibration cycle.

DELETE FROM public.signal_sources
 WHERE origin = 'SEEDED_NCAAF_PRESEASON_824';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES

  -- ─────────────────────────────────────────────────────────────
  -- SP+ magnitude tiers — fires on every game with SP+ data
  -- ─────────────────────────────────────────────────────────────

  -- Huge SP+ favorite (gap 15+) — talent-mismatch signal
  ('ncaaf_sp_plus_huge_fav_home', 'NCAAF', 'preseason_talent', 'rl', 'game',
   'ctx.sp_gap is not None and float(ctx.sp_gap) >= 15',
   '"HOME_RL"',
   'min((float(ctx.sp_gap) - 12) / 20.0, 0.40)',
   '{home_team} SP+ overwhelmingly favors home (gap {sp_gap}) — talent mismatch',
   'NCAAF: home SP+ 15+ points better — talent mismatch prior',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  ('ncaaf_sp_plus_huge_fav_away', 'NCAAF', 'preseason_talent', 'rl', 'game',
   'ctx.sp_gap is not None and float(ctx.sp_gap) <= -15',
   '"AWAY_RL"',
   'min((abs(float(ctx.sp_gap)) - 12) / 20.0, 0.40)',
   '{away_team} SP+ overwhelmingly favors road (gap {sp_gap}) — talent mismatch on the road',
   'NCAAF: away SP+ 15+ points better',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  -- Solid SP+ favorite (gap 8-15) — clear edge but not blowout
  ('ncaaf_sp_plus_solid_fav_home', 'NCAAF', 'preseason_talent', 'ml', 'game',
   'ctx.sp_gap is not None and float(ctx.sp_gap) >= 8 and float(ctx.sp_gap) < 15',
   '"HOME_ML"',
   '0.30',
   '{home_team} SP+ solid edge (gap {sp_gap}) — talent advantage',
   'NCAAF: home SP+ 8-14 points better — clear ML edge',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  ('ncaaf_sp_plus_solid_fav_away', 'NCAAF', 'preseason_talent', 'ml', 'game',
   'ctx.sp_gap is not None and float(ctx.sp_gap) <= -8 and float(ctx.sp_gap) > -15',
   '"AWAY_ML"',
   '0.30',
   '{away_team} SP+ solid edge on road (gap {sp_gap})',
   'NCAAF: away SP+ 8-14 points better',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  -- ─────────────────────────────────────────────────────────────
  -- Returning production — Weeks 1-3 loaded rosters have edge
  -- ─────────────────────────────────────────────────────────────

  ('ncaaf_returning_production_edge_home', 'NCAAF', 'preseason_talent', 'rl', 'game',
   'ctx.home_returning_production is not None and ctx.away_returning_production is not None and (float(ctx.home_returning_production) - float(ctx.away_returning_production)) >= 0.15 and ctx.week is not None and int(ctx.week) <= 3',
   '"HOME_RL"',
   '0.35',
   '{home_team} returning production {home_returning_production} vs {away_team} {away_returning_production} — continuity edge Weeks 1-3',
   'NCAAF Weeks 1-3: home team significantly more returning production',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  ('ncaaf_returning_production_edge_away', 'NCAAF', 'preseason_talent', 'rl', 'game',
   'ctx.home_returning_production is not None and ctx.away_returning_production is not None and (float(ctx.away_returning_production) - float(ctx.home_returning_production)) >= 0.15 and ctx.week is not None and int(ctx.week) <= 3',
   '"AWAY_RL"',
   '0.35',
   '{away_team} returning production {away_returning_production} vs {home_team} {home_returning_production} — road continuity edge',
   'NCAAF Weeks 1-3: away team significantly more returning production',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  -- ─────────────────────────────────────────────────────────────
  -- Home-field advantage — fires on every NCAAF home game (small)
  -- ─────────────────────────────────────────────────────────────

  ('ncaaf_home_field_baseline', 'NCAAF', 'preseason_talent', 'ml', 'game',
   'ctx.home_team is not None and ctx.neutral_site is not True',
   '"HOME_ML"',
   '0.15',
   '{home_team} home-field advantage baseline (CFB HFA ~2.5-3 points)',
   'NCAAF: home-field baseline signal — always fires on non-neutral sites',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  -- ─────────────────────────────────────────────────────────────
  -- Physicality wide-net — softer thresholds than 8/23 signals
  -- ─────────────────────────────────────────────────────────────
  -- 8/23 shipped signals fire on gap >= 20 (strict). These fire on
  -- gap >= 12 (softer) — moderate physical edge worth noting even if
  -- not blowout. Weeks 1-3 only.

  ('ncaaf_ol_edge_moderate_home', 'NCAAF', 'preseason_talent', 'rl', 'game',
   'ctx.ol_dl_weight_gap_home is not None and float(ctx.ol_dl_weight_gap_home) >= 12 and float(ctx.ol_dl_weight_gap_home) < 20 and ctx.week is not None and int(ctx.week) <= 3',
   '"HOME_RL"',
   '0.20',
   '{home_team} OL avg {home_ol_avg_wt}lb has moderate weight edge vs {away_team} DL — early season ground game',
   'NCAAF Weeks 1-3: moderate home OL edge (12-19 lb gap)',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  ('ncaaf_ol_edge_moderate_away', 'NCAAF', 'preseason_talent', 'rl', 'game',
   'ctx.ol_dl_weight_gap_away is not None and float(ctx.ol_dl_weight_gap_away) >= 12 and float(ctx.ol_dl_weight_gap_away) < 20 and ctx.week is not None and int(ctx.week) <= 3',
   '"AWAY_RL"',
   '0.20',
   '{away_team} OL has moderate weight edge on road — early season ground game',
   'NCAAF Weeks 1-3: moderate away OL edge',
   true, 'SEEDED_NCAAF_PRESEASON_824'),

  -- ─────────────────────────────────────────────────────────────
  -- Home + solid favorite + Week 1 = classic chalk spot
  -- ─────────────────────────────────────────────────────────────

  ('ncaaf_home_fav_week1_chalk', 'NCAAF', 'preseason_talent', 'ml', 'game',
   'ctx.week is not None and int(ctx.week) <= 1 and ctx.close_spread is not None and float(ctx.close_spread) < 0 and abs(float(ctx.close_spread)) >= 10 and ctx.neutral_site is not True',
   '"HOME_ML"',
   '0.25',
   '{home_team} big home favorite in Week 1 (spread {close_spread}) — classic chalk spot for talent gap games',
   'NCAAF Week 1: home team as -10+ favorite = chalk spot historically covers ML',
   true, 'SEEDED_NCAAF_PRESEASON_824');

NOTIFY pgrst, 'reload schema';
