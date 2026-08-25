-- NHL Elo-based model signals (2026-08-17).
--
-- Reads nhl_game_context.projected_home_wp + projected_total populated
-- by nhl_game_context.py after NHL Elo model runs.
--
-- Depends on: 20260817_nhl_foundation.sql

ALTER TABLE public.nhl_game_context
  ADD COLUMN IF NOT EXISTS projected_home_wp   NUMERIC,
  ADD COLUMN IF NOT EXISTS projected_total     NUMERIC,
  ADD COLUMN IF NOT EXISTS projected_home_ml   INT,
  ADD COLUMN IF NOT EXISTS elo_home            NUMERIC,
  ADD COLUMN IF NOT EXISTS elo_away            NUMERIC;

DELETE FROM public.signal_sources
 WHERE sport = 'NHL' AND class = 'model'
   AND origin = 'SEEDED_NHL_ELO_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  ('nhl_elo_home_ml_strong', 'NHL', 'model', 'ml', 'game',
   'ctx.projected_home_wp is not None and float(ctx.projected_home_wp) >= 0.58',
   '"HOME_ML"',
   'min((float(ctx.projected_home_wp) - 0.50) * 3.0, 1.0)',
   'Elo home win prob {projected_home_wp} — model favors home',
   'Elo gives home >= 58% (NHL wider tolerance due to variance)',
   true, 'SEEDED_NHL_ELO_817'),

  ('nhl_elo_away_ml_strong', 'NHL', 'model', 'ml', 'game',
   'ctx.projected_home_wp is not None and float(ctx.projected_home_wp) <= 0.42',
   '"AWAY_ML"',
   'min((0.50 - float(ctx.projected_home_wp)) * 3.0, 1.0)',
   'Elo home win prob {projected_home_wp} — model favors away',
   'Elo gives away >= 58%',
   true, 'SEEDED_NHL_ELO_817'),

  ('nhl_elo_over_edge', 'NHL', 'model', 'total', 'game',
   'ctx.projected_total is not None and ctx.close_total is not None and (float(ctx.projected_total) - float(ctx.close_total)) >= 0.6',
   '"OVER"',
   'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 1.5, 1.0)',
   'Elo total projection {projected_total} vs market {close_total} — OVER edge',
   'Model projects total 0.6+ goals above market',
   true, 'SEEDED_NHL_ELO_817'),

  ('nhl_elo_under_edge', 'NHL', 'model', 'total', 'game',
   'ctx.projected_total is not None and ctx.close_total is not None and (float(ctx.projected_total) - float(ctx.close_total)) <= -0.6',
   '"UNDER"',
   'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 1.5, 1.0)',
   'Elo total projection {projected_total} vs market {close_total} — UNDER edge',
   'Model projects total 0.6+ goals below market',
   true, 'SEEDED_NHL_ELO_817'),

  ('nhl_elo_ml_value_dog', 'NHL', 'model', 'ml', 'game',
   'ctx.projected_home_ml is not None and ctx.away_ml_close is not None and ctx.projected_home_wp is not None and float(ctx.projected_home_wp) <= 0.55 and (int(ctx.away_ml_close) - int(ctx.projected_home_ml)) >= 40',
   '"AWAY_ML"',
   '0.5',
   'Away ML {away_ml_close} vs model fair {projected_home_ml} — dog value',
   'Market prices away as bigger dog than Elo says — dog value bet',
   true, 'SEEDED_NHL_ELO_817');

NOTIFY pgrst, 'reload schema';
