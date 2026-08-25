-- Universal season-trend signals for NCAAF, NFL, NBA, MLB (2026-08-17).
--
-- Same 10-signal shape ported across sports. Reads ctx.home_season_cover_pct
-- etc. populated by enrich_team_trends.py.
--
-- These STACK with existing team_form (L4/L5/L10 rolling) signals — season
-- aggregates catch persistent trends, rolling catches recent form. Both
-- fire when they agree = stronger conviction.
--
-- Sports covered here: NCAAF, NFL, NBA, MLB (NCAAB already seeded via
-- earlier 20260817_ncaab_season_signals.sql migration).

-- Cleanup any prior version
DELETE FROM public.signal_sources
 WHERE sport IN ('NCAAF','NFL','NBA','MLB')
   AND class = 'team_form_season'
   AND origin = 'SEEDED_SEASON_TREND_817';

-- Function-like: seed 10 signals per sport with same shape
DO $$
DECLARE
  sp text;
  sports text[] := ARRAY['NCAAF', 'NFL', 'NBA', 'MLB'];
BEGIN
  FOREACH sp IN ARRAY sports LOOP
    INSERT INTO public.signal_sources
      (signal_key, sport, class, market_scope, subject_scope,
       condition_expr, side_expr, strength_expr,
       display_prose_template, description, enabled, origin)
    VALUES
      -- ATS hot / cold (RL / spread)
      ('home_team_ats_hot_season', sp, 'team_form_season', 'rl', 'game',
       'ctx.home_season_cover_pct is not None and float(ctx.home_season_cover_pct) >= 60',
       '"HOME_RL"',
       'min((float(ctx.home_season_cover_pct) - 50) / 20.0, 1.0)',
       '{home_team} covering {home_season_cover_pct}% ATS this season ({home_season_ats_wins}-{home_season_ats_losses})',
       'Home team covers >= 60% season — persistent trend', true, 'SEEDED_SEASON_TREND_817'),

      ('home_team_ats_cold_season', sp, 'team_form_season', 'rl', 'game',
       'ctx.home_season_cover_pct is not None and float(ctx.home_season_cover_pct) <= 42',
       '"AWAY_RL"',
       'min((50 - float(ctx.home_season_cover_pct)) / 15.0, 1.0)',
       '{home_team} only {home_season_cover_pct}% ATS ({home_season_ats_wins}-{home_season_ats_losses})',
       'Cold ATS home team — fade side', true, 'SEEDED_SEASON_TREND_817'),

      ('away_team_ats_hot_season', sp, 'team_form_season', 'rl', 'game',
       'ctx.away_season_cover_pct is not None and float(ctx.away_season_cover_pct) >= 60',
       '"AWAY_RL"',
       'min((float(ctx.away_season_cover_pct) - 50) / 20.0, 1.0)',
       '{away_team} covering {away_season_cover_pct}% ATS ({away_season_ats_wins}-{away_season_ats_losses})',
       'Hot ATS road team', true, 'SEEDED_SEASON_TREND_817'),

      ('away_team_ats_cold_season', sp, 'team_form_season', 'rl', 'game',
       'ctx.away_season_cover_pct is not None and float(ctx.away_season_cover_pct) <= 42',
       '"HOME_RL"',
       'min((50 - float(ctx.away_season_cover_pct)) / 15.0, 1.0)',
       '{away_team} only {away_season_cover_pct}% ATS ({away_season_ats_wins}-{away_season_ats_losses})',
       'Cold ATS road team', true, 'SEEDED_SEASON_TREND_817'),

      -- O/U trend
      ('home_team_over_trend_season', sp, 'team_form_season', 'total', 'game',
       'ctx.home_season_over_pct is not None and float(ctx.home_season_over_pct) >= 60',
       '"OVER"',
       'min((float(ctx.home_season_over_pct) - 50) / 20.0, 1.0)',
       '{home_team} games OVER {home_season_over_pct}% this season',
       'Home OVER trend', true, 'SEEDED_SEASON_TREND_817'),

      ('home_team_under_trend_season', sp, 'team_form_season', 'total', 'game',
       'ctx.home_season_over_pct is not None and float(ctx.home_season_over_pct) <= 40',
       '"UNDER"',
       'min((50 - float(ctx.home_season_over_pct)) / 15.0, 1.0)',
       '{home_team} games UNDER {home_season_over_pct}% overs this season',
       'Home UNDER trend', true, 'SEEDED_SEASON_TREND_817'),

      ('away_team_over_trend_season', sp, 'team_form_season', 'total', 'game',
       'ctx.away_season_over_pct is not None and float(ctx.away_season_over_pct) >= 60',
       '"OVER"',
       'min((float(ctx.away_season_over_pct) - 50) / 20.0, 1.0)',
       '{away_team} games OVER {away_season_over_pct}% this season',
       'Away OVER trend', true, 'SEEDED_SEASON_TREND_817'),

      ('away_team_under_trend_season', sp, 'team_form_season', 'total', 'game',
       'ctx.away_season_over_pct is not None and float(ctx.away_season_over_pct) <= 40',
       '"UNDER"',
       'min((50 - float(ctx.away_season_over_pct)) / 15.0, 1.0)',
       '{away_team} games UNDER {away_season_over_pct}% overs this season',
       'Away UNDER trend', true, 'SEEDED_SEASON_TREND_817'),

      -- Both-teams agreement (high-confidence total)
      ('both_teams_over_trend_season', sp, 'team_form_season', 'total', 'game',
       'ctx.home_season_over_pct is not None and ctx.away_season_over_pct is not None and float(ctx.home_season_over_pct) >= 55 and float(ctx.away_season_over_pct) >= 55',
       '"OVER"',
       '0.6',
       'both {home_team} + {away_team} trend OVER this season',
       'BOTH teams trend OVER >= 55% — high-conviction total',
       true, 'SEEDED_SEASON_TREND_817'),

      ('both_teams_under_trend_season', sp, 'team_form_season', 'total', 'game',
       'ctx.home_season_over_pct is not None and ctx.away_season_over_pct is not None and float(ctx.home_season_over_pct) <= 45 and float(ctx.away_season_over_pct) <= 45',
       '"UNDER"',
       '0.6',
       'both {home_team} + {away_team} trend UNDER this season',
       'BOTH teams trend UNDER <= 45% — high-conviction UNDER',
       true, 'SEEDED_SEASON_TREND_817');
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
