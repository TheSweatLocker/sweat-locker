-- NFL Madden shadow signals (2026-08-24) — talent-prior stack.
--
-- Reads ctx fields populated by enrich_ctx_nfl_madden.py.
-- Sport-scoped so signals only fire on NFL games.
--
-- WHY SHADOW MODE
-- ───────────────
-- Madden ratings are a structured roster-talent proxy. Highest value
-- Weeks 1-3 when EPA sample is thin. Weights start LOW (strength_expr
-- caps at 0.35-0.45) so signals can't dominate scoring while unproven.
-- After 30-45d of graded games, refresh_prop_signal_calibration will
-- re-weight from observed hit rates.
--
-- Signals seeded here (7 total):
--   Team-level: OVR gap, offensive matchup edge (both sides), dual top defense
--   Player-level: QB talent gap, Top 100 QB flag
--   Weeks 1-3 boost: talent mismatch when market is still narrative-driven
--
-- All signals require def_ovr / off_ovr to be populated. NULL-safe.
-- Season-scoped via ctx.season if needed later (v1 assumes current).

DELETE FROM public.signal_sources
 WHERE origin = 'SEEDED_MADDEN_TALENT_824';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES

  -- ─────────────────────────────────────────────────────────────
  -- TEAM-LEVEL: OVR / OFF / DEF matchup signals
  -- ─────────────────────────────────────────────────────────────

  -- Home team meaningfully better roster (OVR gap 6+)
  ('nfl_madden_ovr_edge_home', 'NFL', 'madden_talent', 'ml', 'game',
   'ctx.madden_ovr_gap_home is not None and float(ctx.madden_ovr_gap_home) >= 6',
   '"HOME_ML"',
   'min((float(ctx.madden_ovr_gap_home) - 4) / 12.0, 0.40)',
   '{home_team} Madden OVR {home_madden_ovr} vs {away_team} {away_madden_ovr} — talent edge +{madden_ovr_gap_home}',
   'NFL: home team meaningfully better Madden roster (OVR gap 6+)',
   true, 'SEEDED_MADDEN_TALENT_824'),

  ('nfl_madden_ovr_edge_away', 'NFL', 'madden_talent', 'ml', 'game',
   'ctx.madden_ovr_gap_home is not None and float(ctx.madden_ovr_gap_home) <= -6',
   '"AWAY_ML"',
   'min((abs(float(ctx.madden_ovr_gap_home)) - 4) / 12.0, 0.40)',
   '{away_team} Madden OVR {away_madden_ovr} vs {home_team} {home_madden_ovr} — talent edge on road',
   'NFL: road team meaningfully better Madden roster',
   true, 'SEEDED_MADDEN_TALENT_824'),

  -- Home offense strong AGAINST away defense (OFF gap 8+)
  ('nfl_madden_off_leverage_home', 'NFL', 'madden_talent', 'rl', 'game',
   'ctx.madden_off_gap_home is not None and float(ctx.madden_off_gap_home) >= 8',
   '"HOME_RL"',
   'min((float(ctx.madden_off_gap_home) - 6) / 15.0, 0.45)',
   '{home_team} offense {home_madden_off} vs {away_team} defense {away_madden_def} — matchup leverage',
   'NFL: home offense meaningfully outrates away defense',
   true, 'SEEDED_MADDEN_TALENT_824'),

  ('nfl_madden_off_leverage_away', 'NFL', 'madden_talent', 'rl', 'game',
   'ctx.madden_off_gap_away is not None and float(ctx.madden_off_gap_away) >= 8',
   '"AWAY_RL"',
   'min((float(ctx.madden_off_gap_away) - 6) / 15.0, 0.45)',
   '{away_team} offense {away_madden_off} vs {home_team} defense {home_madden_def} — road matchup leverage',
   'NFL: away offense meaningfully outrates home defense',
   true, 'SEEDED_MADDEN_TALENT_824'),

  -- Dual top defenses → UNDER (both defenses 85+)
  ('nfl_madden_dual_top_defense_under', 'NFL', 'madden_talent', 'total', 'game',
   'ctx.home_madden_def is not None and ctx.away_madden_def is not None and float(ctx.home_madden_def) >= 85 and float(ctx.away_madden_def) >= 85',
   '"UNDER"',
   '0.40',
   'dual top defenses ({home_team} DEF {home_madden_def} / {away_team} DEF {away_madden_def}) — points at a premium',
   'NFL: both defenses rated 85+ — favors UNDER',
   true, 'SEEDED_MADDEN_TALENT_824'),

  -- ─────────────────────────────────────────────────────────────
  -- PLAYER-LEVEL: QB talent + Top 100 boosts
  -- ─────────────────────────────────────────────────────────────

  -- Home QB meaningfully better (delta 8+)
  ('nfl_madden_qb_advantage_home', 'NFL', 'madden_talent', 'ml', 'game',
   'ctx.madden_qb_delta_home is not None and float(ctx.madden_qb_delta_home) >= 8',
   '"HOME_ML"',
   'min((float(ctx.madden_qb_delta_home) - 6) / 14.0, 0.40)',
   '{home_team} QB Madden {home_qb_madden_ovr} vs {away_team} QB {away_qb_madden_ovr} — QB edge +{madden_qb_delta_home}',
   'NFL: home QB significantly better rated than opponent',
   true, 'SEEDED_MADDEN_TALENT_824'),

  ('nfl_madden_qb_advantage_away', 'NFL', 'madden_talent', 'ml', 'game',
   'ctx.madden_qb_delta_home is not None and float(ctx.madden_qb_delta_home) <= -8',
   '"AWAY_ML"',
   'min((abs(float(ctx.madden_qb_delta_home)) - 6) / 14.0, 0.40)',
   '{away_team} QB Madden {away_qb_madden_ovr} vs {home_team} QB {home_qb_madden_ovr} — QB edge on road',
   'NFL: road QB significantly better rated than home',
   true, 'SEEDED_MADDEN_TALENT_824'),

  -- ─────────────────────────────────────────────────────────────
  -- WEEKS 1-3 BOOST: talent mismatch when market is narrative-driven
  -- ─────────────────────────────────────────────────────────────
  -- Big OVR gap in early season = talent prior overweight vs EPA sample.
  -- Higher weight cap because EPA hasn't stabilized yet.

  ('nfl_madden_talent_gap_early_home', 'NFL', 'madden_talent', 'ml', 'game',
   'ctx.madden_ovr_gap_home is not None and float(ctx.madden_ovr_gap_home) >= 10 and ctx.week is not None and int(ctx.week) <= 3',
   '"HOME_ML"',
   '0.45',
   '{home_team} big talent edge (Madden +{madden_ovr_gap_home}) in Week {week} — market underweights talent early',
   'NFL Weeks 1-3: home has large Madden OVR gap (10+) — talent prior overweights the market',
   true, 'SEEDED_MADDEN_TALENT_824'),

  ('nfl_madden_talent_gap_early_away', 'NFL', 'madden_talent', 'ml', 'game',
   'ctx.madden_ovr_gap_home is not None and float(ctx.madden_ovr_gap_home) <= -10 and ctx.week is not None and int(ctx.week) <= 3',
   '"AWAY_ML"',
   '0.45',
   '{away_team} big talent edge (Madden +{madden_ovr_gap_home}) in Week {week} — road talent overweight',
   'NFL Weeks 1-3: away has large Madden OVR gap (10+)',
   true, 'SEEDED_MADDEN_TALENT_824');

NOTIFY pgrst, 'reload schema';
