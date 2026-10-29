INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin,
    hit_rate_pct, sample_n
)
SELECT
    'nhl_puckline_public_follow_home',
    'NHL', 'money_flow', 'rl', 'game',
    'ctx.splits_summary is not None and ctx.splits_summary.get("rl") is not None and (ctx.splits_summary["rl"].get("HOME") or {}).get("bets_pct_avg") is not None and float(ctx.splits_summary["rl"]["HOME"]["bets_pct_avg"]) >= 60.0',
    '"HOME_RL"',
    'min(0.30 + (float(ctx.splits_summary["rl"]["HOME"]["bets_pct_avg"]) - 60.0) / 100.0, 0.50)',
    '{home_team} is carrying the public puckline money; following heavy public on the NHL puckline measured 66.1 pct on 118 plays',
    'NHL: back the HOME puckline when it holds 60 pct or more of public bets. Measured FOLLOW (not fade): 66.10 pct vs 62.40 pct breakeven, n=118, split-half +8.04 pct / +5.49 pct.',
    true, 'SEEDED_NHL_PUCKLINE_PUBLIC_FOLLOW_1029',
    66.1, 118
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nhl_puckline_public_follow_home' AND sport = 'NHL'
);

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin,
    hit_rate_pct, sample_n
)
SELECT
    'nhl_puckline_public_follow_away',
    'NHL', 'money_flow', 'rl', 'game',
    'ctx.splits_summary is not None and ctx.splits_summary.get("rl") is not None and (ctx.splits_summary["rl"].get("AWAY") or {}).get("bets_pct_avg") is not None and float(ctx.splits_summary["rl"]["AWAY"]["bets_pct_avg"]) >= 60.0',
    '"AWAY_RL"',
    'min(0.30 + (float(ctx.splits_summary["rl"]["AWAY"]["bets_pct_avg"]) - 60.0) / 100.0, 0.50)',
    '{away_team} is carrying the public puckline money; following heavy public on the NHL puckline measured 66.1 pct on 118 plays',
    'NHL: back the AWAY puckline when it holds 60 pct or more of public bets. Measured FOLLOW (not fade): 66.10 pct vs 62.40 pct breakeven, n=118, split-half +8.04 pct / +5.49 pct.',
    true, 'SEEDED_NHL_PUCKLINE_PUBLIC_FOLLOW_1029',
    66.1, 118
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nhl_puckline_public_follow_away' AND sport = 'NHL'
);
