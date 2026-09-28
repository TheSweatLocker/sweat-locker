-- 2026-09-27b · Give the NHL Monte Carlo a vote in the pick.
--
-- Andy: "we should have 5 models at least for each sport, each having a
-- take or pushing narrative right, then engine picks the picks based on
-- model data, money flow, situational records..."
--
-- 20260927 shipped an NHL goal projection and Monte Carlo, and they are
-- written to nhl_game_context and rendered on the card. But they had NO
-- SIGNAL ROWS, and the ensemble only sees models through signal_sources —
-- so both were displayed and neither could influence the pick.
--
-- The soundness check that exposed it, on 2026-09-29 MTL @ TOR:
--
--     pick     Toronto Maple Leafs ML  (HOME)
--     MC       p(home) 0.4701          <- the sim says MONTREAL wins
--     sources  away_goalie_elite_gsaa, nhl_home_pp_vs_weak_pk
--
-- The engine picked the home side on two historical-trend patterns while
-- the newest and most direct model on the card disagreed, silently. Same
-- shape on VAN @ EDM, where MC put Edmonton at 0.651 against a market
-- 0.733 — a clear overpriced-favourite read that reached nobody.
--
-- ── EDGE VS THE PRICE, NOT "THE MODEL LIKES HOME" ──
-- These compare the simulated probability to the MARKET-IMPLIED one, so
-- the signal only fires when the sim disagrees with the price. A signal
-- that fires whenever the model likes a side just re-votes for the
-- favourite, which is how a board ends up 91% chalk.
--
-- The 4-point threshold is deliberately above the vig on a typical NHL
-- moneyline: below that the "edge" is inside the hold and is noise.
-- Strength scales over 15 points, so a 4pp disagreement is a weak vote
-- and a 19pp one is full weight.
--
-- Implied probability is computed inline because signal_expr exposes only
-- a builtin whitelist (abs / float / min / max / round / math) and has no
-- helper for American odds. Verbose, but it keeps the whole definition in
-- one row where it can be audited.
--
-- ── ALSO FIXES A NOW-WRONG LABEL ──
-- nhl_elo_over_edge / nhl_elo_under_edge read ctx.projected_total, which
-- as of 20260927 is written by the calibrated goal projection rather than
-- by Elo (Elo keeps projected_home_wp as its own separate take). Their
-- prose still said "Elo total projection", crediting the wrong model on
-- the card. The signal keys are left alone — renaming them would orphan
-- their graded history in the weight registry — but the prose now names
-- what actually produced the number.

-- NOTE ON THE UPSERT SHAPE. signal_sources has NO unique constraint on
-- signal_key — the same key legitimately exists for several sports (e.g.
-- v4_model_spread on MLB and v4_spread_nfl on NFL), and the ensemble log
-- reports collisions on (signal_name, sport). So ON CONFLICT (signal_key)
-- fails with 42P10. Guarded with NOT EXISTS on (signal_key, sport)
-- instead, which is idempotent without depending on a constraint that
-- does not exist.

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr, display_prose_template,
   enabled, origin)
SELECT v.* FROM (VALUES
  ('nhl_mc_home_ml_edge', 'NHL', 'model', 'ml', 'game',
   'ctx.mc_probabilities is not None and ctx.mc_probabilities.get("mc_p_home") is not None and ctx.close_home_ml is not None and float(ctx.mc_probabilities["mc_p_home"]) - (abs(float(ctx.close_home_ml))/(abs(float(ctx.close_home_ml))+100.0) if float(ctx.close_home_ml) < 0 else 100.0/(float(ctx.close_home_ml)+100.0)) > 0.04',
   '"HOME_ML"',
   'min((float(ctx.mc_probabilities["mc_p_home"]) - (abs(float(ctx.close_home_ml))/(abs(float(ctx.close_home_ml))+100.0) if float(ctx.close_home_ml) < 0 else 100.0/(float(ctx.close_home_ml)+100.0))) / 0.15, 1.0)',
   '10k-sim win probability runs ahead of the price on the home side',
   true, 'NHL_MC_WIRE_927'),

  ('nhl_mc_away_ml_edge', 'NHL', 'model', 'ml', 'game',
   'ctx.mc_probabilities is not None and ctx.mc_probabilities.get("mc_p_home") is not None and ctx.close_away_ml is not None and (1.0 - float(ctx.mc_probabilities["mc_p_home"])) - (abs(float(ctx.close_away_ml))/(abs(float(ctx.close_away_ml))+100.0) if float(ctx.close_away_ml) < 0 else 100.0/(float(ctx.close_away_ml)+100.0)) > 0.04',
   '"AWAY_ML"',
   'min(((1.0 - float(ctx.mc_probabilities["mc_p_home"])) - (abs(float(ctx.close_away_ml))/(abs(float(ctx.close_away_ml))+100.0) if float(ctx.close_away_ml) < 0 else 100.0/(float(ctx.close_away_ml)+100.0))) / 0.15, 1.0)',
   '10k-sim win probability runs ahead of the price on the road side',
   true, 'NHL_MC_WIRE_927'),

  ('nhl_mc_over_edge', 'NHL', 'model', 'total', 'game',
   'ctx.mc_probabilities is not None and ctx.mc_probabilities.get("mc_expected_total") is not None and ctx.close_total is not None and float(ctx.mc_probabilities["mc_expected_total"]) > float(ctx.close_total) + 0.3',
   '"OVER"',
   'min((float(ctx.mc_probabilities["mc_expected_total"]) - float(ctx.close_total)) / 1.0, 1.0)',
   'simulated total {mc_expected_total} sits above the market {close_total}',
   true, 'NHL_MC_WIRE_927'),

  ('nhl_mc_under_edge', 'NHL', 'model', 'total', 'game',
   'ctx.mc_probabilities is not None and ctx.mc_probabilities.get("mc_expected_total") is not None and ctx.close_total is not None and float(ctx.mc_probabilities["mc_expected_total"]) < float(ctx.close_total) - 0.3',
   '"UNDER"',
   'min((float(ctx.close_total) - float(ctx.mc_probabilities["mc_expected_total"])) / 1.0, 1.0)',
   'simulated total {mc_expected_total} sits below the market {close_total}',
   true, 'NHL_MC_WIRE_927')
) AS v(signal_key, sport, class, market_scope, subject_scope,
       condition_expr, side_expr, strength_expr, display_prose_template,
       enabled, origin)
WHERE NOT EXISTS (
  SELECT 1 FROM public.signal_sources s
   WHERE s.signal_key = v.signal_key AND s.sport = v.sport
);

-- projected_total is no longer Elo's number; name the right model.
UPDATE public.signal_sources
   SET display_prose_template =
       'goal projection {projected_total} vs market {close_total} — OVER edge'
 WHERE signal_key = 'nhl_elo_over_edge';

UPDATE public.signal_sources
   SET display_prose_template =
       'goal projection {projected_total} vs market {close_total} — UNDER edge'
 WHERE signal_key = 'nhl_elo_under_edge';

NOTIFY pgrst, 'reload schema';
