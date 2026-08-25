-- NBA model signals from Elo-driven projections (2026-08-17).
--
-- Seeds signals that read nba_game_context.projected_spread and
-- projected_home_wp — populated by nba_game_context.py after Elo model
-- is applied. These are the FIRST NBA game-level signals beyond
-- team_form_season, giving the ensemble real model input.
--
-- Depends on: 20260817_nba_foundation.sql (creates projected_spread
-- + primary_play columns)

DELETE FROM public.signal_sources
 WHERE sport = 'NBA' AND class = 'model'
   AND origin = 'SEEDED_NBA_MODEL_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  -- ── Elo edge on spread ────────────────────────────────────
  ('nba_elo_edge_home_spread', 'NBA', 'model', 'rl', 'game',
   'ctx.projected_spread is not None and ctx.close_spread is not None and (float(ctx.projected_spread) - float(ctx.close_spread)) <= -2.0',
   '"HOME_RL"',
   'min(abs(float(ctx.projected_spread) - float(ctx.close_spread)) / 6.0, 1.0)',
   'Elo projects {projected_spread}, market {close_spread} — {edge_pts}pt home edge',
   'Model projects home spread more negative than market by 2+ points — HOME_RL edge',
   true, 'SEEDED_NBA_MODEL_817'),

  ('nba_elo_edge_away_spread', 'NBA', 'model', 'rl', 'game',
   'ctx.projected_spread is not None and ctx.close_spread is not None and (float(ctx.projected_spread) - float(ctx.close_spread)) >= 2.0',
   '"AWAY_RL"',
   'min(abs(float(ctx.projected_spread) - float(ctx.close_spread)) / 6.0, 1.0)',
   'Elo projects {projected_spread}, market {close_spread} — {edge_pts}pt away edge',
   'Model projects home spread less favorable than market by 2+ points — AWAY_RL edge',
   true, 'SEEDED_NBA_MODEL_817'),

  -- ── Model win-prob confidence ─────────────────────────────
  ('nba_elo_home_ml_strong', 'NBA', 'model', 'ml', 'game',
   'ctx.projected_home_wp is not None and float(ctx.projected_home_wp) >= 0.60',
   '"HOME_ML"',
   'min((float(ctx.projected_home_wp) - 0.50) * 3.0, 1.0)',
   'Elo home win prob {projected_home_wp} — model favors home',
   'Model gives home team >= 60% win probability',
   true, 'SEEDED_NBA_MODEL_817'),

  ('nba_elo_away_ml_strong', 'NBA', 'model', 'ml', 'game',
   'ctx.projected_home_wp is not None and float(ctx.projected_home_wp) <= 0.40',
   '"AWAY_ML"',
   'min((0.50 - float(ctx.projected_home_wp)) * 3.0, 1.0)',
   'Elo home win prob {projected_home_wp} — model favors away',
   'Model gives away team >= 60% win probability',
   true, 'SEEDED_NBA_MODEL_817'),

  -- ── Total edge (pace + scoring) ───────────────────────────
  ('nba_elo_over_edge', 'NBA', 'model', 'total', 'game',
   'ctx.projected_total is not None and ctx.close_total is not None and (float(ctx.projected_total) - float(ctx.close_total)) >= 4.0',
   '"OVER"',
   'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 10.0, 1.0)',
   'Elo total projection {projected_total} vs market {close_total} — OVER edge',
   'Model projects total 4+ points above market — OVER edge',
   true, 'SEEDED_NBA_MODEL_817'),

  ('nba_elo_under_edge', 'NBA', 'model', 'total', 'game',
   'ctx.projected_total is not None and ctx.close_total is not None and (float(ctx.projected_total) - float(ctx.close_total)) <= -4.0',
   '"UNDER"',
   'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 10.0, 1.0)',
   'Elo total projection {projected_total} vs market {close_total} — UNDER edge',
   'Model projects total 4+ points below market — UNDER edge',
   true, 'SEEDED_NBA_MODEL_817');

-- Also add columns nba_game_context needs for Elo output (add if missing
-- since foundation migration may not be applied yet).
ALTER TABLE public.nba_game_context
  ADD COLUMN IF NOT EXISTS projected_spread    NUMERIC,
  ADD COLUMN IF NOT EXISTS projected_total     NUMERIC,
  ADD COLUMN IF NOT EXISTS projected_home_wp   NUMERIC,
  ADD COLUMN IF NOT EXISTS elo_home            NUMERIC,
  ADD COLUMN IF NOT EXISTS elo_away            NUMERIC,
  ADD COLUMN IF NOT EXISTS elo_updated_at      TIMESTAMPTZ;

NOTIFY pgrst, 'reload schema';
