-- NCAAB season-tendency signals (2026-08-17).
--
-- Ports the team_form pattern to NCAAB using SEASON aggregates from
-- teamrankings.com (per-game backfill blocked — 0/5911 games have odds
-- data). Season aggregates are stickier than L5/L10 rolling but still
-- predictive per teamrankings' trend analyses (which they publish
-- specifically because these tendencies persist).
--
-- Reads ctx.home_season_cover_pct / home_season_over_pct etc. populated
-- by pull_ncaab_teamrankings_trends.py + join in ncaab_game_context.py.
--
-- Depends on: 20260817_ncaab_team_trends.sql

DELETE FROM public.signal_sources
 WHERE sport = 'NCAAB' AND class = 'team_form'
   AND origin = 'SEEDED_NCAAB_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  -- ── ATS tendencies (season) ────────────────────────────────
  ('home_team_ats_hot_season', 'NCAAB', 'team_form', 'rl', 'game',
   'ctx.home_season_cover_pct is not None and float(ctx.home_season_cover_pct) >= 60',
   '"HOME_RL"',
   'min((float(ctx.home_season_cover_pct) - 50) / 20.0, 1.0)',
   '{home_team} covering {home_season_cover_pct}% ATS this season ({home_season_ats_wins}-{home_season_ats_losses})',
   'Home team covers >= 60% this season — season-persistent trend',
   true, 'SEEDED_NCAAB_817'),

  ('home_team_ats_cold_season', 'NCAAB', 'team_form', 'rl', 'game',
   'ctx.home_season_cover_pct is not None and float(ctx.home_season_cover_pct) <= 42',
   '"AWAY_RL"',
   'min((50 - float(ctx.home_season_cover_pct)) / 15.0, 1.0)',
   '{home_team} only {home_season_cover_pct}% ATS season ({home_season_ats_wins}-{home_season_ats_losses}) — fade side',
   'Home team covers <= 42% — fade side',
   true, 'SEEDED_NCAAB_817'),

  ('away_team_ats_hot_season', 'NCAAB', 'team_form', 'rl', 'game',
   'ctx.away_season_cover_pct is not None and float(ctx.away_season_cover_pct) >= 60',
   '"AWAY_RL"',
   'min((float(ctx.away_season_cover_pct) - 50) / 20.0, 1.0)',
   '{away_team} covering {away_season_cover_pct}% ATS this season ({away_season_ats_wins}-{away_season_ats_losses})',
   'Away team covers >= 60% — persistent trend',
   true, 'SEEDED_NCAAB_817'),

  ('away_team_ats_cold_season', 'NCAAB', 'team_form', 'rl', 'game',
   'ctx.away_season_cover_pct is not None and float(ctx.away_season_cover_pct) <= 42',
   '"HOME_RL"',
   'min((50 - float(ctx.away_season_cover_pct)) / 15.0, 1.0)',
   '{away_team} only {away_season_cover_pct}% ATS ({away_season_ats_wins}-{away_season_ats_losses})',
   'Away team covers <= 42% — fade side',
   true, 'SEEDED_NCAAB_817'),

  -- ── O/U tendencies (season) ────────────────────────────────
  ('home_team_over_trend_season', 'NCAAB', 'team_form', 'total', 'game',
   'ctx.home_season_over_pct is not None and float(ctx.home_season_over_pct) >= 60',
   '"OVER"',
   'min((float(ctx.home_season_over_pct) - 50) / 20.0, 1.0)',
   '{home_team} games going OVER {home_season_over_pct}% this season',
   'Home team games trend OVER — pace/scoring signal',
   true, 'SEEDED_NCAAB_817'),

  ('home_team_under_trend_season', 'NCAAB', 'team_form', 'total', 'game',
   'ctx.home_season_over_pct is not None and float(ctx.home_season_over_pct) <= 40',
   '"UNDER"',
   'min((50 - float(ctx.home_season_over_pct)) / 15.0, 1.0)',
   '{home_team} games going UNDER {home_season_over_pct}% overs this season',
   'Home team games trend UNDER — defense/pace signal',
   true, 'SEEDED_NCAAB_817'),

  ('away_team_over_trend_season', 'NCAAB', 'team_form', 'total', 'game',
   'ctx.away_season_over_pct is not None and float(ctx.away_season_over_pct) >= 60',
   '"OVER"',
   'min((float(ctx.away_season_over_pct) - 50) / 20.0, 1.0)',
   '{away_team} games going OVER {away_season_over_pct}% this season',
   'Away team games trend OVER',
   true, 'SEEDED_NCAAB_817'),

  ('away_team_under_trend_season', 'NCAAB', 'team_form', 'total', 'game',
   'ctx.away_season_over_pct is not None and float(ctx.away_season_over_pct) <= 40',
   '"UNDER"',
   'min((50 - float(ctx.away_season_over_pct)) / 15.0, 1.0)',
   '{away_team} games going UNDER {away_season_over_pct}% overs',
   'Away team games trend UNDER',
   true, 'SEEDED_NCAAB_817'),

  -- ── Stacked ATS + O/U agreement (real edge) ────────────────
  ('both_teams_over_trend', 'NCAAB', 'team_form', 'total', 'game',
   'ctx.home_season_over_pct is not None and ctx.away_season_over_pct is not None and float(ctx.home_season_over_pct) >= 55 and float(ctx.away_season_over_pct) >= 55',
   '"OVER"',
   '0.6',
   'both {home_team} ({home_season_over_pct}% over) + {away_team} ({away_season_over_pct}% over) trend OVER',
   'BOTH teams trend OVER >= 55% season — high-confidence total pick',
   true, 'SEEDED_NCAAB_817'),

  ('both_teams_under_trend', 'NCAAB', 'team_form', 'total', 'game',
   'ctx.home_season_over_pct is not None and ctx.away_season_over_pct is not None and float(ctx.home_season_over_pct) <= 45 and float(ctx.away_season_over_pct) <= 45',
   '"UNDER"',
   '0.6',
   'both {home_team} ({home_season_over_pct}% over) + {away_team} ({away_season_over_pct}% over) trend UNDER',
   'BOTH teams trend UNDER <= 45% — high-confidence UNDER',
   true, 'SEEDED_NCAAB_817');

NOTIFY pgrst, 'reload schema';
