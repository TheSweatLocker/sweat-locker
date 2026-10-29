ALTER TABLE public.nba_game_context
  ADD COLUMN IF NOT EXISTS home_efg_pct numeric,
  ADD COLUMN IF NOT EXISTS away_efg_pct numeric,
  ADD COLUMN IF NOT EXISTS home_opp_efg_pct numeric,
  ADD COLUMN IF NOT EXISTS away_opp_efg_pct numeric,
  ADD COLUMN IF NOT EXISTS home_tov_pct numeric,
  ADD COLUMN IF NOT EXISTS away_tov_pct numeric,
  ADD COLUMN IF NOT EXISTS home_opp_tov_pct numeric,
  ADD COLUMN IF NOT EXISTS away_opp_tov_pct numeric,
  ADD COLUMN IF NOT EXISTS home_ft_rate numeric,
  ADD COLUMN IF NOT EXISTS away_ft_rate numeric,
  ADD COLUMN IF NOT EXISTS home_opp_ft_rate numeric,
  ADD COLUMN IF NOT EXISTS away_opp_ft_rate numeric,
  ADD COLUMN IF NOT EXISTS home_orb_pct numeric,
  ADD COLUMN IF NOT EXISTS away_orb_pct numeric;

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin
)
SELECT
    'nba_shooting_exploit_home',
    'NBA', 'matchup', 'rl', 'game',
    'ctx.home_efg_pct is not None and ctx.away_opp_efg_pct is not None and float(ctx.home_efg_pct) >= 0.5423 and float(ctx.away_opp_efg_pct) >= 0.5538',
    '"HOME_RL"',
    'min(0.30 + (float(ctx.home_efg_pct) - 0.5423) * 4.0 + (float(ctx.away_opp_efg_pct) - 0.5538) * 4.0, 0.50)',
    '{home_team} shooting {home_efg_pct} eFG (top third) into a defense allowing {away_opp_efg_pct} - shooting mismatch',
    'NBA: home shoots top-third eFG vs an opponent conceding top-third eFG',
    true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nba_shooting_exploit_home' AND sport = 'NBA'
);

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin
)
SELECT
    'nba_shooting_exploit_away',
    'NBA', 'matchup', 'rl', 'game',
    'ctx.away_efg_pct is not None and ctx.home_opp_efg_pct is not None and float(ctx.away_efg_pct) >= 0.5423 and float(ctx.home_opp_efg_pct) >= 0.5538',
    '"AWAY_RL"',
    'min(0.30 + (float(ctx.away_efg_pct) - 0.5423) * 4.0 + (float(ctx.home_opp_efg_pct) - 0.5538) * 4.0, 0.50)',
    '{away_team} shooting {away_efg_pct} eFG into a defense allowing {home_opp_efg_pct} - shooting mismatch on the road',
    'NBA: away shoots top-third eFG vs an opponent conceding top-third eFG',
    true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nba_shooting_exploit_away' AND sport = 'NBA'
);

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin
)
SELECT
    'nba_ball_security_home',
    'NBA', 'matchup', 'rl', 'game',
    'ctx.home_tov_pct is not None and ctx.away_opp_tov_pct is not None and float(ctx.home_tov_pct) <= 0.1320 and float(ctx.away_opp_tov_pct) <= 0.1303',
    '"HOME_RL"',
    'min(0.28 + (0.1320 - float(ctx.home_tov_pct)) * 6.0 + (0.1303 - float(ctx.away_opp_tov_pct)) * 6.0, 0.46)',
    '{home_team} turns it over on just {home_tov_pct} of possessions and {away_team} forces only {away_opp_tov_pct} - free possessions',
    'NBA: home protects the ball vs an opponent that does not force turnovers',
    true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nba_ball_security_home' AND sport = 'NBA'
);

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin
)
SELECT
    'nba_ball_security_away',
    'NBA', 'matchup', 'rl', 'game',
    'ctx.away_tov_pct is not None and ctx.home_opp_tov_pct is not None and float(ctx.away_tov_pct) <= 0.1320 and float(ctx.home_opp_tov_pct) <= 0.1303',
    '"AWAY_RL"',
    'min(0.28 + (0.1320 - float(ctx.away_tov_pct)) * 6.0 + (0.1303 - float(ctx.home_opp_tov_pct)) * 6.0, 0.46)',
    '{away_team} turns it over on just {away_tov_pct} of possessions and {home_team} forces only {home_opp_tov_pct}',
    'NBA: away protects the ball vs an opponent that does not force turnovers',
    true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nba_ball_security_away' AND sport = 'NBA'
);

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin
)
SELECT
    'nba_foul_leverage_home',
    'NBA', 'matchup', 'rl', 'game',
    'ctx.home_ft_rate is not None and ctx.away_opp_ft_rate is not None and float(ctx.home_ft_rate) >= 0.2371 and float(ctx.away_opp_ft_rate) >= 0.2512',
    '"HOME_RL"',
    'min(0.26 + (float(ctx.home_ft_rate) - 0.2371) * 3.0 + (float(ctx.away_opp_ft_rate) - 0.2512) * 3.0, 0.42)',
    '{home_team} gets to the line at {home_ft_rate} against a {away_team} defense conceding {away_opp_ft_rate} - whistle leverage',
    'NBA: home draws fouls at a top-third rate vs a foul-prone opponent',
    true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nba_foul_leverage_home' AND sport = 'NBA'
);

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin
)
SELECT
    'nba_foul_leverage_away',
    'NBA', 'matchup', 'rl', 'game',
    'ctx.away_ft_rate is not None and ctx.home_opp_ft_rate is not None and float(ctx.away_ft_rate) >= 0.2371 and float(ctx.home_opp_ft_rate) >= 0.2512',
    '"AWAY_RL"',
    'min(0.26 + (float(ctx.away_ft_rate) - 0.2371) * 3.0 + (float(ctx.home_opp_ft_rate) - 0.2512) * 3.0, 0.42)',
    '{away_team} gets to the line at {away_ft_rate} against a {home_team} defense conceding {home_opp_ft_rate}',
    'NBA: away draws fouls at a top-third rate vs a foul-prone opponent',
    true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nba_foul_leverage_away' AND sport = 'NBA'
);
