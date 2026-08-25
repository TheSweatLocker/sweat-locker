-- Seed 10 NCAAF team_form signals patterned on MLB (2026-08-17).
--
-- Ports the MLB team_form playbook to NCAAF ahead of the 2026-08-22
-- season opener. Uses L5 window (vs MLB's L10) because NCAAF regular
-- season is 12 games. Adjusted thresholds:
--   * hot / cold at 4+/1- of 5 (was 7+/3- of 10 for MLB)
--   * over/under trend at 4+/1- of 5
--   * covers_as_fav/dog gate at 60% (same as MLB) with min-sample
--     handled in backfill_ncaaf_team_tendencies.py (MIN_FAVDOG_SAMPLE=3)
--
-- Depends on: 20260817_ncaaf_team_tendencies.sql (columns must exist).
--
-- These land as DISCOVERY tier weight (w=0.20 via signal_registry
-- default) and auto-promote to VALIDATED (w~1.0) as hit-rate data
-- accrues through the season.
--
-- Idempotency: DELETE first, then INSERT. Re-running the migration
-- cleanly replaces these 10 seeds (safer than ON CONFLICT which
-- requires a specific unique constraint we can't verify from here).

DELETE FROM public.signal_sources
 WHERE sport = 'NCAAF' AND class = 'team_form' AND origin = 'SEEDED';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, condition_expr, side_expr,
   strength_expr, display_prose_template, description, enabled, origin)
VALUES
  ('home_team_ats_hot', 'NCAAF', 'team_form', 'rl',
   'ctx.home_ats_last5 is not None and int(ctx.home_ats_last5) >= 4',
   '"HOME_RL"',
   'min((int(ctx.home_ats_last5) - 2) / 3.0, 1.0)',
   '{home_team} covering ATS {home_ats_last5}-{home_ats_last5_losses} L5 — hot cover trend',
   'Hot cover team keeps covering — MLB pattern ported to NCAAF L5',
   true, 'SEEDED'),

  ('home_team_ats_cold', 'NCAAF', 'team_form', 'rl',
   'ctx.home_ats_last5 is not None and int(ctx.home_ats_last5) <= 1',
   '"AWAY_RL"',
   'min((3 - int(ctx.home_ats_last5)) / 3.0, 1.0)',
   '{home_team} only covering ATS {home_ats_last5}-{home_ats_last5_losses} L5 — cold ATS',
   'Cold ATS home team — fade side (back away RL)',
   true, 'SEEDED'),

  ('away_team_ats_hot', 'NCAAF', 'team_form', 'rl',
   'ctx.away_ats_last5 is not None and int(ctx.away_ats_last5) >= 4',
   '"AWAY_RL"',
   'min((int(ctx.away_ats_last5) - 2) / 3.0, 1.0)',
   '{away_team} covering ATS {away_ats_last5}-{away_ats_last5_losses} L5 — hot cover trend',
   'Hot ATS road team — back their RL',
   true, 'SEEDED'),

  ('home_team_over_trend', 'NCAAF', 'team_form', 'total',
   'ctx.home_ou_last5_overs is not None and int(ctx.home_ou_last5_overs) >= 4',
   '"OVER"',
   'min((int(ctx.home_ou_last5_overs) - 2) / 3.0, 1.0)',
   '{home_team} games going OVER {home_ou_last5_overs}/5 recently',
   'Home team on an OVER heater — back total OVER',
   true, 'SEEDED'),

  ('home_team_under_trend', 'NCAAF', 'team_form', 'total',
   'ctx.home_ou_last5_overs is not None and int(ctx.home_ou_last5_overs) <= 1',
   '"UNDER"',
   'min((3 - int(ctx.home_ou_last5_overs)) / 3.0, 1.0)',
   '{home_team} games staying UNDER {home_ou_last5_overs}/5 overs recently',
   'Home team on an UNDER trend — back total UNDER',
   true, 'SEEDED'),

  ('away_team_over_trend', 'NCAAF', 'team_form', 'total',
   'ctx.away_ou_last5_overs is not None and int(ctx.away_ou_last5_overs) >= 4',
   '"OVER"',
   'min((int(ctx.away_ou_last5_overs) - 2) / 3.0, 1.0)',
   '{away_team} games going OVER {away_ou_last5_overs}/5 recently',
   'Away team on an OVER heater — back total OVER',
   true, 'SEEDED'),

  ('away_team_under_trend', 'NCAAF', 'team_form', 'total',
   'ctx.away_ou_last5_overs is not None and int(ctx.away_ou_last5_overs) <= 1',
   '"UNDER"',
   'min((3 - int(ctx.away_ou_last5_overs)) / 3.0, 1.0)',
   '{away_team} games staying UNDER {away_ou_last5_overs}/5 overs recently',
   'Away team on an UNDER trend — back total UNDER',
   true, 'SEEDED'),

  ('home_covers_as_fav', 'NCAAF', 'team_form', 'rl',
   'ctx.home_covers_as_fav_pct is not None and float(ctx.home_covers_as_fav_pct) >= 60 and ctx.close_spread is not None and float(ctx.close_spread) < 0',
   '"HOME_RL"',
   '0.4',
   '{home_team} covers as favorite {home_covers_as_fav_pct}% this season',
   'Home team reliably covers when favored — back their RL',
   true, 'SEEDED'),

  ('away_covers_as_dog', 'NCAAF', 'team_form', 'rl',
   'ctx.away_covers_as_dog_pct is not None and float(ctx.away_covers_as_dog_pct) >= 60 and ctx.close_spread is not None and float(ctx.close_spread) < 0',
   '"AWAY_RL"',
   '0.4',
   '{away_team} covers as underdog {away_covers_as_dog_pct}% this season',
   'Away team reliably covers as dog — back their RL',
   true, 'SEEDED'),

  ('home_fades_own_ml_hot', 'NCAAF', 'team_form', 'ml',
   'ctx.home_ml_last5 is not None and int(ctx.home_ml_last5) >= 4',
   '"HOME_ML"',
   'min((int(ctx.home_ml_last5) - 2) / 3.0, 1.0)',
   '{home_team} {home_ml_last5}-{home_ml_last5_losses} straight up L5 — hot form',
   'Hot ML home team — back their ML (name intentionally matches MLB seed; semantics under review)',
   true, 'SEEDED');

NOTIFY pgrst, 'reload schema';
