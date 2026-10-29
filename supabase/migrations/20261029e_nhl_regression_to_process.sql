INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin,
    hit_rate_pct, sample_n
)
SELECT
    'nhl_regression_due_home',
    'NHL', 'regression', 'ml', 'game',
    'ctx.home_xgf_per60 is not None and ctx.home_xga_per60 is not None and ctx.home_l10_goals_per_game is not None and ctx.home_l10_goals_against_per_game is not None and ctx.away_xgf_per60 is not None and ctx.away_xga_per60 is not None and ctx.away_l10_goals_per_game is not None and ctx.away_l10_goals_against_per_game is not None and (((-(((float(ctx.home_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.home_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.home_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.home_xga_per60) - 2.4822) / 0.4102))) - ((-(((float(ctx.away_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.away_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.away_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.away_xga_per60) - 2.4822) / 0.4102)))) >= 1.97',
    '"HOME_ML"',
    'min(0.30 + (abs((((-(((float(ctx.home_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.home_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.home_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.home_xga_per60) - 2.4822) / 0.4102))) - ((-(((float(ctx.away_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.away_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.away_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.away_xga_per60) - 2.4822) / 0.4102))))) - 1.97) / 10.0, 0.60)',
    '{home_team} has been scoring below and conceding above its underlying xG process - the side regression favours; this measured 62.0 pct on 71 NHL moneylines',
    'NHL REGRESSION TO PROCESS. z(L10 goals for) - z(xGF/60) is how far a team is scoring ABOVE its process; z(L10 goals against) - z(xGA/60) how far it concedes above. The team with the larger (-off_gap + def_gap) is the one due to IMPROVE. Fires when the two-sided edge reaches 1.97. MEASURED leak-free (tendency stamp on or before the game) and PRICED off closing moneylines: 62.0 pct on n=71 vs 55.0 pct breakeven, +7.01 pp, ROI +11.11 pct, and 37 pct of its picks are DOGS - where the control "always the favourite" is 0 pct dogs and +0.58 pp. Holds split-half on the moneyline (+8.44 pp then +4.66 pp). NOT seeded for the puckline: positive at every threshold there but it FAILS split-half (+6.08 pp then -0.69 pp). Constants are measured means and sds over 500 team-games and need refreshing as the season grows. Replayed against 301 live rows before seeding: 53 HOME fires, 40 AWAY, 0 eval errors, mutually exclusive, and the replay reproduced the analysis (n=71, +7.01 pp).',
    true, 'SEEDED_NHL_REGRESSION_1029',
    62.0, 71
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nhl_regression_due_home' AND sport = 'NHL'
);

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, description, enabled, origin,
    hit_rate_pct, sample_n
)
SELECT
    'nhl_regression_due_away',
    'NHL', 'regression', 'ml', 'game',
    'ctx.home_xgf_per60 is not None and ctx.home_xga_per60 is not None and ctx.home_l10_goals_per_game is not None and ctx.home_l10_goals_against_per_game is not None and ctx.away_xgf_per60 is not None and ctx.away_xga_per60 is not None and ctx.away_l10_goals_per_game is not None and ctx.away_l10_goals_against_per_game is not None and (((-(((float(ctx.home_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.home_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.home_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.home_xga_per60) - 2.4822) / 0.4102))) - ((-(((float(ctx.away_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.away_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.away_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.away_xga_per60) - 2.4822) / 0.4102)))) <= -1.97',
    '"AWAY_ML"',
    'min(0.30 + (abs((((-(((float(ctx.home_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.home_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.home_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.home_xga_per60) - 2.4822) / 0.4102))) - ((-(((float(ctx.away_l10_goals_per_game) - 2.9220) / 0.6452) - ((float(ctx.away_xgf_per60) - 2.4998) / 0.4445))) + (((float(ctx.away_l10_goals_against_per_game) - 3.0894) / 0.5847) - ((float(ctx.away_xga_per60) - 2.4822) / 0.4102))))) - 1.97) / 10.0, 0.60)',
    '{away_team} has been scoring below and conceding above its underlying xG process - the side regression favours; this measured 62.0 pct on 71 NHL moneylines',
    'NHL REGRESSION TO PROCESS. z(L10 goals for) - z(xGF/60) is how far a team is scoring ABOVE its process; z(L10 goals against) - z(xGA/60) how far it concedes above. The team with the larger (-off_gap + def_gap) is the one due to IMPROVE. Fires when the two-sided edge reaches 1.97. MEASURED leak-free (tendency stamp on or before the game) and PRICED off closing moneylines: 62.0 pct on n=71 vs 55.0 pct breakeven, +7.01 pp, ROI +11.11 pct, and 37 pct of its picks are DOGS - where the control "always the favourite" is 0 pct dogs and +0.58 pp. Holds split-half on the moneyline (+8.44 pp then +4.66 pp). NOT seeded for the puckline: positive at every threshold there but it FAILS split-half (+6.08 pp then -0.69 pp). Constants are measured means and sds over 500 team-games and need refreshing as the season grows. Replayed against 301 live rows before seeding: 53 HOME fires, 40 AWAY, 0 eval errors, mutually exclusive, and the replay reproduced the analysis (n=71, +7.01 pp).',
    true, 'SEEDED_NHL_REGRESSION_1029',
    62.0, 71
WHERE NOT EXISTS (
    SELECT 1 FROM public.signal_sources
    WHERE signal_key = 'nhl_regression_due_away' AND sport = 'NHL'
);
