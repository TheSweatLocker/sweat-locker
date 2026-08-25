-- Seed 10 NFL team_form signals patterned on MLB/NCAAF (2026-08-17).
--
-- L4 window (17-game season). Adjusted thresholds:
--   * hot / cold at 3+/1- of 4
--   * over/under trend at 3+/1- of 4
--   * covers_as_fav/dog at 60% (MIN_FAVDOG_SAMPLE=3 in backfill)

DELETE FROM public.signal_sources
 WHERE sport = 'NFL' AND class = 'team_form' AND origin = 'SEEDED';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, condition_expr, side_expr,
   strength_expr, display_prose_template, description, enabled, origin)
VALUES
  ('home_team_ats_hot', 'NFL', 'team_form', 'rl',
   'ctx.home_ats_last4 is not None and int(ctx.home_ats_last4) >= 3',
   '"HOME_RL"',
   'min((int(ctx.home_ats_last4) - 2) / 2.0, 1.0)',
   '{home_team} covering ATS {home_ats_last4}-{home_ats_last4_losses} L4 — hot cover trend',
   'Hot cover team keeps covering — MLB pattern ported to NFL L4',
   true, 'SEEDED'),

  ('home_team_ats_cold', 'NFL', 'team_form', 'rl',
   'ctx.home_ats_last4 is not None and int(ctx.home_ats_last4) <= 1',
   '"AWAY_RL"',
   'min((2 - int(ctx.home_ats_last4)) / 2.0, 1.0)',
   '{home_team} only covering ATS {home_ats_last4}-{home_ats_last4_losses} L4 — cold ATS',
   'Cold ATS home team — fade side (back away RL)',
   true, 'SEEDED'),

  ('away_team_ats_hot', 'NFL', 'team_form', 'rl',
   'ctx.away_ats_last4 is not None and int(ctx.away_ats_last4) >= 3',
   '"AWAY_RL"',
   'min((int(ctx.away_ats_last4) - 2) / 2.0, 1.0)',
   '{away_team} covering ATS {away_ats_last4}-{away_ats_last4_losses} L4 — hot cover trend',
   'Hot ATS road team — back their RL',
   true, 'SEEDED'),

  ('home_team_over_trend', 'NFL', 'team_form', 'total',
   'ctx.home_ou_last4_overs is not None and int(ctx.home_ou_last4_overs) >= 3',
   '"OVER"',
   'min((int(ctx.home_ou_last4_overs) - 2) / 2.0, 1.0)',
   '{home_team} games going OVER {home_ou_last4_overs}/4 recently',
   'Home team on an OVER heater — back total OVER',
   true, 'SEEDED'),

  ('home_team_under_trend', 'NFL', 'team_form', 'total',
   'ctx.home_ou_last4_overs is not None and int(ctx.home_ou_last4_overs) <= 1',
   '"UNDER"',
   'min((2 - int(ctx.home_ou_last4_overs)) / 2.0, 1.0)',
   '{home_team} games staying UNDER {home_ou_last4_overs}/4 overs recently',
   'Home team on an UNDER trend — back total UNDER',
   true, 'SEEDED'),

  ('away_team_over_trend', 'NFL', 'team_form', 'total',
   'ctx.away_ou_last4_overs is not None and int(ctx.away_ou_last4_overs) >= 3',
   '"OVER"',
   'min((int(ctx.away_ou_last4_overs) - 2) / 2.0, 1.0)',
   '{away_team} games going OVER {away_ou_last4_overs}/4 recently',
   'Away team on an OVER heater — back total OVER',
   true, 'SEEDED'),

  ('away_team_under_trend', 'NFL', 'team_form', 'total',
   'ctx.away_ou_last4_overs is not None and int(ctx.away_ou_last4_overs) <= 1',
   '"UNDER"',
   'min((2 - int(ctx.away_ou_last4_overs)) / 2.0, 1.0)',
   '{away_team} games staying UNDER {away_ou_last4_overs}/4 overs recently',
   'Away team on an UNDER trend — back total UNDER',
   true, 'SEEDED'),

  ('home_covers_as_fav', 'NFL', 'team_form', 'rl',
   'ctx.home_covers_as_fav_pct is not None and float(ctx.home_covers_as_fav_pct) >= 60 and ctx.close_spread is not None and float(ctx.close_spread) < 0',
   '"HOME_RL"',
   '0.4',
   '{home_team} covers as favorite {home_covers_as_fav_pct}% recently',
   'Home team reliably covers when favored — back their RL',
   true, 'SEEDED'),

  ('away_covers_as_dog', 'NFL', 'team_form', 'rl',
   'ctx.away_covers_as_dog_pct is not None and float(ctx.away_covers_as_dog_pct) >= 60 and ctx.close_spread is not None and float(ctx.close_spread) < 0',
   '"AWAY_RL"',
   '0.4',
   '{away_team} covers as underdog {away_covers_as_dog_pct}% recently',
   'Away team reliably covers as dog — back their RL',
   true, 'SEEDED'),

  ('home_fades_own_ml_hot', 'NFL', 'team_form', 'ml',
   'ctx.home_ml_last4 is not None and int(ctx.home_ml_last4) >= 3',
   '"HOME_ML"',
   'min((int(ctx.home_ml_last4) - 2) / 2.0, 1.0)',
   '{home_team} {home_ml_last4}-{home_ml_last4_losses} straight up L4 — hot form',
   'Hot ML home team — back their ML',
   true, 'SEEDED');

NOTIFY pgrst, 'reload schema';
