-- 20261029a · NBA GETS A MATCHUP AXIS — "GOOD AT X vs BAD AT STOPPING X"
--
-- Andy, 2026-10-28: "Engine for example should be like: Better offense,
-- rushing exploit against bad rush defense, money flow split slight edge on
-- sharp side, home team covers at home more often than away though, team is
-- on hot streak. Based on all these and other signals I assess the eagle to
-- cover tonight. In a nutshell for evey sport."
--
-- Audited against that standard, NBA was the worst of the five sports.
-- Enabled GAME signals, exist / fire / measured:
--
--   category                     MLB      NFL     NCAAF      NBA      NHL
--   1 team quality            10/2/0    4/4/0   16/11/0   0/0/0    0/0/0
--   2 MATCHUP exploit          0/0/0    3/1/0    0/0/0    0/0/0    0/0/0
--   3 money flow / sharp       5/0/1    3/0/0    3/2/0    0/0/0    0/0/0
--   4 situational home/away    9/1/0    8/8/0    8/6/0    6/0/0    9/8/0
--   5 form / hot streak       23/2/0   22/13/0  20/16/0  12/0/0   10/1/0
--
-- NBA is 0/0/0 on quality, matchup AND money flow, and nothing fires in the
-- two categories where signals exist. Its 14 uncategorised signals are all
-- Elo — the price restated — which is why its read says "Elo model has the
-- Hawks at 78%" where NCAAF's already says "offense running +0.184 EPA/play
-- vs a defense allowing +0.039 — a 14.5-point efficiency chasm".
--
-- ══ WHY NOT JUST off_rating vs def_rating. MEASURED, AND IT IS THE MARKET ══
-- The obvious NBA analogue of "better offense against a bad defense" is
-- home_off_rating vs away_def_rating. It is a dead end, and the algebra says
-- why:
--
--     (ho + ad) - (ao + hd)   ==   (ho - hd) - (ao - ad)
--     "the matchup"                 the NET-RATING differential
--
-- They are the SAME EXPRESSION, because off and def ratings are both
-- points-per-100-possessions and symmetric. Measured on 63 games with
-- point-in-time ratings:
--
--     corr with home margin           +0.341
--     corr with margin vs the SPREAD  +0.070   <- no edge
--     corr with the LINE ITSELF       +0.688   <- the market already has it
--
-- So seeding that would have added another price proxy, which is exactly the
-- mechanism behind the favourite lock. Football is different: a bad team can
-- have a good run game against a bad run defence, so rush-vs-rush is a real
-- second axis. Basketball needs the FOUR FACTORS for that.
--
-- ══ WHAT THESE SIGNALS USE ══════════════════════════════════════════════
-- nba_team_stats carries, for the CURRENT season, all 30 teams:
--
--     efg_pct 0.461-0.646   opp_efg_pct 0.479-0.602     complete pair
--     tov_pct 0.108-0.202   opp_tov_pct 0.106-0.204     complete pair
--     ft_rate 0.155-0.308   opp_ft_rate 0.167-0.332     complete pair
--     orb_pct 0.180-0.357   opp_orb_pct  NULL on 30/30  <- HALF MISSING
--
-- Three complete pairs, so three signals. The rebounding exploit is NOT
-- seeded: opp_orb_pct is NULL on every row of the current season, so the
-- condition could never be true and a permanently-silent signal is worse
-- than none (it reads as tested-and-quiet). Fixing that scraper field is a
-- separate item and the fourth signal lands with it.
--
-- `efg_pct` was already in enrich_team_stats' select and in its FIELDS merge
-- and was then never written to the row — fetched and thrown away. This
-- migration adds the columns that write now has to land in.
--
-- ══ THRESHOLDS ARE MEASURED PERCENTILES, NOT ROUND NUMBERS ══════════════
-- Taken from the live 30-team distribution on 2026-10-28, p70 for "good at
-- it" and p30 for "protects it". NOT re-fitted per run: re-learning a
-- threshold has cost this repo 2-8 points every time it has been measured
-- (the fade 55.7->47.7, the wind cutoff 55.8->53.6).
--
--     efg_pct     p70 0.5423      opp_efg_pct p70 0.5538
--     tov_pct     p30 0.1320      opp_tov_pct p30 0.1303
--     ft_rate     p70 0.2371      opp_ft_rate p70 0.2512
--
-- ══ THESE ARE UNMEASURED AND SAY SO ════════════════════════════════════
-- hit_rate_pct and sample_n are NULL, so edge_weight gives them the
-- unmeasured floor and the 2026-09-03 credibility gate keeps a LEAN resting
-- only on them at COVERAGE. They CANNOT be validated tonight: nba_team_stats
-- is a current-season snapshot, so testing it against games already in that
-- snapshot is the leak that produced the fake NCAAF 67.8% (z=+4.07) retracted
-- on 09-26. They are seeded to FIRE and accrue attribution, and the
-- per-sport refit wired on 10-28 will weight them against a direction-only
-- control once n>=10.
--
-- READ-NEUTRAL on existing data: new nullable columns, new signal rows.
-- Idempotent. Safe to re-run.

BEGIN;

-- ── 1. the columns enrich_team_stats now writes ──────────────────────────
ALTER TABLE public.nba_game_context
  ADD COLUMN IF NOT EXISTS home_efg_pct      numeric,
  ADD COLUMN IF NOT EXISTS away_efg_pct      numeric,
  ADD COLUMN IF NOT EXISTS home_opp_efg_pct  numeric,
  ADD COLUMN IF NOT EXISTS away_opp_efg_pct  numeric,
  ADD COLUMN IF NOT EXISTS home_tov_pct      numeric,
  ADD COLUMN IF NOT EXISTS away_tov_pct      numeric,
  ADD COLUMN IF NOT EXISTS home_opp_tov_pct  numeric,
  ADD COLUMN IF NOT EXISTS away_opp_tov_pct  numeric,
  ADD COLUMN IF NOT EXISTS home_ft_rate      numeric,
  ADD COLUMN IF NOT EXISTS away_ft_rate      numeric,
  ADD COLUMN IF NOT EXISTS home_opp_ft_rate  numeric,
  ADD COLUMN IF NOT EXISTS away_opp_ft_rate  numeric,
  ADD COLUMN IF NOT EXISTS home_orb_pct      numeric,
  ADD COLUMN IF NOT EXISTS away_orb_pct      numeric;

COMMENT ON COLUMN public.nba_game_context.home_opp_efg_pct IS
  'eFG% the HOME team''s opponents shoot against it — i.e. how well it '
  'defends the shot. Paired with away_efg_pct to express a shooting '
  'mismatch. See 20261029a.';

-- ── 2. the three matchup signals, both sides ─────────────────────────────
-- side_expr is a quoted literal because the scorer evaluates it as an
-- expression (see ncaaf_ground_leverage_home for the pattern this copies).
INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  -- SHOOTING: we shoot well, they let people shoot well
  ('nba_shooting_exploit_home', 'NBA', 'matchup', 'rl', 'game',
   'ctx.home_efg_pct is not None and ctx.away_opp_efg_pct is not None '
   'and float(ctx.home_efg_pct) >= 0.5423 '
   'and float(ctx.away_opp_efg_pct) >= 0.5538',
   '"HOME_RL"',
   'min(0.30 + (float(ctx.home_efg_pct) - 0.5423) * 4.0 '
   '+ (float(ctx.away_opp_efg_pct) - 0.5538) * 4.0, 0.50)',
   '{home_team} shooting {home_efg_pct} eFG (top third) into a defense '
   'allowing {away_opp_efg_pct} — shooting mismatch',
   'NBA: home shoots top-third eFG vs an opponent conceding top-third eFG',
   true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'),

  ('nba_shooting_exploit_away', 'NBA', 'matchup', 'rl', 'game',
   'ctx.away_efg_pct is not None and ctx.home_opp_efg_pct is not None '
   'and float(ctx.away_efg_pct) >= 0.5423 '
   'and float(ctx.home_opp_efg_pct) >= 0.5538',
   '"AWAY_RL"',
   'min(0.30 + (float(ctx.away_efg_pct) - 0.5423) * 4.0 '
   '+ (float(ctx.home_opp_efg_pct) - 0.5538) * 4.0, 0.50)',
   '{away_team} shooting {away_efg_pct} eFG into a defense allowing '
   '{home_opp_efg_pct} — shooting mismatch on the road',
   'NBA: away shoots top-third eFG vs an opponent conceding top-third eFG',
   true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'),

  -- BALL SECURITY: we protect it, they do not take it away
  ('nba_ball_security_home', 'NBA', 'matchup', 'rl', 'game',
   'ctx.home_tov_pct is not None and ctx.away_opp_tov_pct is not None '
   'and float(ctx.home_tov_pct) <= 0.1320 '
   'and float(ctx.away_opp_tov_pct) <= 0.1303',
   '"HOME_RL"',
   'min(0.28 + (0.1320 - float(ctx.home_tov_pct)) * 6.0 '
   '+ (0.1303 - float(ctx.away_opp_tov_pct)) * 6.0, 0.46)',
   '{home_team} turns it over on just {home_tov_pct} of possessions and '
   '{away_team} forces only {away_opp_tov_pct} — free possessions',
   'NBA: home protects the ball vs an opponent that does not force turnovers',
   true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'),

  ('nba_ball_security_away', 'NBA', 'matchup', 'rl', 'game',
   'ctx.away_tov_pct is not None and ctx.home_opp_tov_pct is not None '
   'and float(ctx.away_tov_pct) <= 0.1320 '
   'and float(ctx.home_opp_tov_pct) <= 0.1303',
   '"AWAY_RL"',
   'min(0.28 + (0.1320 - float(ctx.away_tov_pct)) * 6.0 '
   '+ (0.1303 - float(ctx.home_opp_tov_pct)) * 6.0, 0.46)',
   '{away_team} turns it over on just {away_tov_pct} of possessions and '
   '{home_team} forces only {home_opp_tov_pct}',
   'NBA: away protects the ball vs an opponent that does not force turnovers',
   true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'),

  -- WHISTLE: we get to the line, they foul
  ('nba_foul_leverage_home', 'NBA', 'matchup', 'rl', 'game',
   'ctx.home_ft_rate is not None and ctx.away_opp_ft_rate is not None '
   'and float(ctx.home_ft_rate) >= 0.2371 '
   'and float(ctx.away_opp_ft_rate) >= 0.2512',
   '"HOME_RL"',
   'min(0.26 + (float(ctx.home_ft_rate) - 0.2371) * 3.0 '
   '+ (float(ctx.away_opp_ft_rate) - 0.2512) * 3.0, 0.42)',
   '{home_team} gets to the line at {home_ft_rate} against a {away_team} '
   'defense conceding {away_opp_ft_rate} — whistle leverage',
   'NBA: home draws fouls at a top-third rate vs a foul-prone opponent',
   true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'),

  ('nba_foul_leverage_away', 'NBA', 'matchup', 'rl', 'game',
   'ctx.away_ft_rate is not None and ctx.home_opp_ft_rate is not None '
   'and float(ctx.away_ft_rate) >= 0.2371 '
   'and float(ctx.home_opp_ft_rate) >= 0.2512',
   '"AWAY_RL"',
   'min(0.26 + (float(ctx.away_ft_rate) - 0.2371) * 3.0 '
   '+ (float(ctx.home_opp_ft_rate) - 0.2512) * 3.0, 0.42)',
   '{away_team} gets to the line at {away_ft_rate} against a {home_team} '
   'defense conceding {home_opp_ft_rate}',
   'NBA: away draws fouls at a top-third rate vs a foul-prone opponent',
   true, 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029')
ON CONFLICT (signal_key) DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- VERIFY
--
--   -- the six signals landed and are enabled
--   SELECT signal_key, market_scope, enabled, sample_n
--     FROM public.signal_sources
--    WHERE origin = 'SEEDED_NBA_FOUR_FACTOR_MATCHUP_1029'
--    ORDER BY signal_key;
--
--   -- the columns exist
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name = 'nba_game_context'
--      AND column_name LIKE '%efg_pct%' OR column_name LIKE '%tov_pct%';
--
-- THEN, to populate them and see the signals fire:
--
--   python nba_game_context.py --days 2
--     -> expect "four factors on N/N rows"
--   python ensemble_scorer.py --sport NBA --verbose
--     -> expect nba_shooting_exploit_* / nba_ball_security_* /
--        nba_foul_leverage_* in the contributions, class=matchup
