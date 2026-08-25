-- NHL v1.0 signal_sources seed (2026-08-17).
--
-- Seeds base NHL signals for the plug-in ensemble. Same pattern as
-- MLB/NCAAF/NFL. Covers:
--   * 10 team_form signals (ATS/OU/ML tendencies) — same shape as
--     other sports, adjusted for L5 window
--   * 3 model signals — pull from Odds API-derived projections when
--     nhl_game_context adds projected_puckline / projected_total
--     (Phase 2; scaffolding lands now)
--   * 1 goalie signal — starter GSAA (Goals Saved Above Average) as
--     matchup edge (nhl_game_context.home_goalie_gsaa)
--
-- Pattern: subject_scope='game', class='team_form'/'goalie'/'model'.
-- All start DISCOVERY tier (w=0.20), auto-promote as hit-rate accrues.

DELETE FROM public.signal_sources
 WHERE sport = 'NHL' AND class IN ('team_form', 'goalie', 'model')
   AND origin = 'SEEDED_NHL_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  -- ── team_form: ATS hot/cold (puckline) ─────────────────────────
  ('home_team_ats_hot', 'NHL', 'team_form', 'rl', 'game',
   'ctx.home_ats_last5 is not None and int(ctx.home_ats_last5) >= 4',
   '"HOME_RL"',
   'min((int(ctx.home_ats_last5) - 2) / 3.0, 1.0)',
   '{home_team} covering puckline {home_ats_last5}-{home_ats_last5_losses} L5 — hot ATS',
   'Hot ATS home team continues covering. Back their puckline.',
   true, 'SEEDED_NHL_817'),

  ('home_team_ats_cold', 'NHL', 'team_form', 'rl', 'game',
   'ctx.home_ats_last5 is not None and int(ctx.home_ats_last5) <= 1',
   '"AWAY_RL"',
   'min((3 - int(ctx.home_ats_last5)) / 3.0, 1.0)',
   '{home_team} only {home_ats_last5}-{home_ats_last5_losses} ATS L5 — cold',
   'Cold ATS home team — back away puckline.',
   true, 'SEEDED_NHL_817'),

  ('away_team_ats_hot', 'NHL', 'team_form', 'rl', 'game',
   'ctx.away_ats_last5 is not None and int(ctx.away_ats_last5) >= 4',
   '"AWAY_RL"',
   'min((int(ctx.away_ats_last5) - 2) / 3.0, 1.0)',
   '{away_team} covering puckline {away_ats_last5}-{away_ats_last5_losses} L5',
   'Hot ATS road team — back their puckline.',
   true, 'SEEDED_NHL_817'),

  -- ── team_form: O/U trends ─────────────────────────────────────
  ('home_team_over_trend', 'NHL', 'team_form', 'total', 'game',
   'ctx.home_ou_last5_overs is not None and int(ctx.home_ou_last5_overs) >= 4',
   '"OVER"',
   'min((int(ctx.home_ou_last5_overs) - 2) / 3.0, 1.0)',
   '{home_team} games {home_ou_last5_overs}/5 OVER recently',
   'Home team on an OVER heater — back total OVER.',
   true, 'SEEDED_NHL_817'),

  ('home_team_under_trend', 'NHL', 'team_form', 'total', 'game',
   'ctx.home_ou_last5_overs is not None and int(ctx.home_ou_last5_overs) <= 1',
   '"UNDER"',
   'min((3 - int(ctx.home_ou_last5_overs)) / 3.0, 1.0)',
   '{home_team} games {home_ou_last5_overs}/5 OVERs (mostly UNDERs)',
   'Home team on an UNDER trend — back total UNDER.',
   true, 'SEEDED_NHL_817'),

  ('away_team_over_trend', 'NHL', 'team_form', 'total', 'game',
   'ctx.away_ou_last5_overs is not None and int(ctx.away_ou_last5_overs) >= 4',
   '"OVER"',
   'min((int(ctx.away_ou_last5_overs) - 2) / 3.0, 1.0)',
   '{away_team} games {away_ou_last5_overs}/5 OVER recently',
   'Away team on an OVER heater — back total OVER.',
   true, 'SEEDED_NHL_817'),

  ('away_team_under_trend', 'NHL', 'team_form', 'total', 'game',
   'ctx.away_ou_last5_overs is not None and int(ctx.away_ou_last5_overs) <= 1',
   '"UNDER"',
   'min((3 - int(ctx.away_ou_last5_overs)) / 3.0, 1.0)',
   '{away_team} games {away_ou_last5_overs}/5 OVERs (mostly UNDERs)',
   'Away team on an UNDER trend — back total UNDER.',
   true, 'SEEDED_NHL_817'),

  -- ── team_form: covers as fav / dog ────────────────────────────
  ('home_covers_as_fav', 'NHL', 'team_form', 'rl', 'game',
   'ctx.home_covers_as_fav_pct is not None and float(ctx.home_covers_as_fav_pct) >= 60 and ctx.close_puckline is not None and float(ctx.close_puckline) < 0',
   '"HOME_RL"',
   '0.4',
   '{home_team} covers as favorite {home_covers_as_fav_pct}% this season',
   'Home team reliably covers puckline when favored.',
   true, 'SEEDED_NHL_817'),

  ('away_covers_as_dog', 'NHL', 'team_form', 'rl', 'game',
   'ctx.away_covers_as_dog_pct is not None and float(ctx.away_covers_as_dog_pct) >= 60 and ctx.close_puckline is not None and float(ctx.close_puckline) < 0',
   '"AWAY_RL"',
   '0.4',
   '{away_team} covers as underdog {away_covers_as_dog_pct}% this season',
   'Away team reliably covers puckline as dog.',
   true, 'SEEDED_NHL_817'),

  ('home_fades_own_ml_hot', 'NHL', 'team_form', 'ml', 'game',
   'ctx.home_ml_last5 is not None and int(ctx.home_ml_last5) >= 4',
   '"HOME_ML"',
   'min((int(ctx.home_ml_last5) - 2) / 3.0, 1.0)',
   '{home_team} {home_ml_last5}-{home_ml_last5_losses} SU L5 — hot ML',
   'Hot ML home team — back their ML.',
   true, 'SEEDED_NHL_817'),

  -- ── goalie: elite starter matchup edge ────────────────────────
  ('home_goalie_elite_gsaa', 'NHL', 'goalie', 'ml', 'game',
   'ctx.home_goalie_gsaa is not None and float(ctx.home_goalie_gsaa) >= 5.0',
   '"HOME_ML"',
   'min(float(ctx.home_goalie_gsaa) / 15.0, 1.0)',
   '{home_goalie} elite GSAA {home_goalie_gsaa} — starter matchup edge',
   'Home goalie has Goals Saved Above Average >= 5 — real matchup edge.',
   true, 'SEEDED_NHL_817'),

  ('away_goalie_elite_gsaa', 'NHL', 'goalie', 'ml', 'game',
   'ctx.away_goalie_gsaa is not None and float(ctx.away_goalie_gsaa) >= 5.0',
   '"AWAY_ML"',
   'min(float(ctx.away_goalie_gsaa) / 15.0, 1.0)',
   '{away_goalie} elite GSAA {away_goalie_gsaa} — starter matchup edge',
   'Away goalie has Goals Saved Above Average >= 5 — real matchup edge.',
   true, 'SEEDED_NHL_817'),

  -- ── model: rest / back-to-back edges ──────────────────────────
  ('home_b2b_penalty', 'NHL', 'model', 'ml', 'game',
   'ctx.home_rest_days is not None and int(ctx.home_rest_days) == 0',
   '"AWAY_ML"',
   '0.4',
   '{home_team} playing back-to-back — rest disadvantage',
   'Home team on 0 days rest (back-to-back) — historical fade signal.',
   true, 'SEEDED_NHL_817'),

  ('away_b2b_penalty', 'NHL', 'model', 'ml', 'game',
   'ctx.away_rest_days is not None and int(ctx.away_rest_days) == 0',
   '"HOME_ML"',
   '0.4',
   '{away_team} playing back-to-back — rest disadvantage',
   'Away team on 0 days rest (back-to-back) — historical fade signal.',
   true, 'SEEDED_NHL_817'),

  ('home_long_rest_advantage', 'NHL', 'model', 'ml', 'game',
   'ctx.home_rest_days is not None and int(ctx.home_rest_days) >= 3 and (ctx.away_rest_days is None or int(ctx.away_rest_days) <= 1)',
   '"HOME_ML"',
   '0.3',
   '{home_team} rested {home_rest_days} days vs tired {away_team}',
   'Home team has 3+ days rest while opponent has 1 or fewer — advantage.',
   true, 'SEEDED_NHL_817');

NOTIFY pgrst, 'reload schema';
