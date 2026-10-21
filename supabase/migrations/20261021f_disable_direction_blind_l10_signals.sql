-- == 2026-10-21 - DISABLE THE DIRECTION-BLIND L10 SIGNALS (NHL + NBA) ==
--
-- nhl_player_l10_extreme / nba_player_l10_extreme and
-- nhl_prop_l10_extreme  / nba_prop_l10_extreme share the SAME condition
-- expression, the same signal class (prop_form) and the same strength
-- formula. They differ in exactly one place, side_expr:
--
--   *_prop_l10_extreme    (KEEP, direction-aware)
--     'BACK' if (l10 >= 8 and direction == 'over')
--             or (l10 <= 2 and direction == 'under') else 'FADE'
--
--   *_player_l10_extreme  (DISABLE, direction-blind)
--     'BACK' if l10 >= 8 else 'FADE'
--
-- The second one ignores which side the prop is. On an UNDER prop for a
-- player who has cleared the line 8 of his last 10, it says BACK -- back the
-- under on a player who keeps going over. A signal that reads a magnitude
-- without reading the side it applies to has no direction control, and the
-- measurement agrees it is the worse of the two. Calibrated on NHL history
-- inside the publishable odds band:
--
--   nhl_prop_l10_extreme    59.9% vs 60.4% breakeven  n=3,511   -0.5pp
--   nhl_player_l10_extreme  58.9% vs 61.0% breakeven  n=2,142   -2.0pp
--
-- Neither beats its price, so this is not promoting one over the other on
-- merit; it is removing a redundant, direction-blind copy. The remaining one
-- stays enabled and goes to weight 0 once calibrate_prop_signals writes its
-- baseline.
--
-- THE DEPTH-GATE COST OF KEEPING BOTH. Because they share a condition AND a
-- class, whenever they agree they contribute TWO entries to the scorer's
-- `win_unique_signals` count while adding one piece of information. PRIME
-- asks for 4 unique signals; two free duplicates deliver half of that
-- (project_panel_is_one_voice_1014 -- a consensus is free when the voices are
-- the same number). When they disagree they split BACK against FADE and
-- cancel, which is noise either way.
--
-- Precedent for disabling a signal this way: 20260927a_disable_nfl_v4_signals.

UPDATE public.signal_sources
   SET enabled = FALSE,
       updated_at = now()
 WHERE signal_key IN ('nhl_player_l10_extreme', 'nba_player_l10_extreme')
   AND subject_scope IN ('prop', 'player_prop');

-- The stale registry claim has to go with it.
--
-- signal_registry holds nhl_player_l10_extreme at hit_rate 67.0, n 21,241,
-- tier DISCOVERY. Against edge_weight's flat BREAKEVEN of 0.524 that is a
-- +14.6pp edge and resolves to a weight near 0.97 -- a near-maximum voice.
-- The 67% was measured without the publishable odds band and without
-- comparing to the price the signal actually faced; inside the band and
-- against its own breakeven it is 58.9% vs 61.0%, i.e. under water.
--
-- calibrate_prop_signals only reads ENABLED signals, so once the rows above
-- are disabled it will never refresh these registry rows, and the false 67%
-- would sit here indefinitely ready to be resurrected at full weight if
-- anything re-enabled the signal.
--
-- hit_rate is set to the MEASURED in-band figure rather than nulled, because
-- nulling would hand it edge_weight's no-record prior of 0.20 instead of 0,
-- and because the number is known. baseline_pct carries the price it has to
-- beat, so edge_weight returns 0.0 for it. NBA's row has never been measured
-- (sample_n 0), so it is left unmeasured rather than given a borrowed value.
UPDATE public.signal_registry
   SET hit_rate = 58.9,
       sample_n = 2142,
       baseline_pct = 61.0,
       tier = 'ANTI_VALIDATED',
       edge_pp = -2.0,
       last_computed_at = now(),
       origin = 'calibrate_prop_signals',
       notes = 'DISABLED 2026-10-21: direction-blind side_expr (says BACK on '
               'an under when l10>=8). Superseded by nhl_prop_l10_extreme. '
               'Prior row claimed 67.0% n=21241 DISCOVERY, measured without '
               'the publishable odds band and without price comparison, '
               'which resolved to weight ~0.97. In-band vs its own 61.0% '
               'breakeven it is 58.9% on n=2142.'
 WHERE signal_name = 'nhl_player_l10_extreme'
   AND sport = 'NHL';

NOTIFY pgrst, 'reload schema';
