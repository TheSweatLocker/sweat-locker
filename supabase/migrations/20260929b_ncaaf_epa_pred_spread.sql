-- ════════════════════════════════════════════════════════════════════════
-- 20260929b · NCAAF: give the discarded EPA spread a column of its own
--
-- WHY
-- ---
-- ncaaf_game_context.compute_projections computes an EPA-based spread and
-- then throws it away:
--
--     epa_spread = round((h_net_epa - a_net_epa) * K_PTS_EPA + hfa, 2)
--     out['sp_plus_pred_spread'] = round(projected_spread, 2)   # <- not epa
--     # Store EPA as second lens (called "sp_plus_pred_spread" but really EPA)
--     # Cleaner: add explicit epa_pred_spread field, but keep for consistency
--
-- `epa_spread` is assigned and never read. The line below it copies the
-- PRIMARY projection into the column, and the trailing comments describe the
-- intent rather than the code. This migration is that "cleaner" field.
--
-- WHAT THIS COST
-- --------------
-- Because projected_spread already uses SP+ when both teams have it,
-- sp_plus_pred_spread is a byte-identical copy of projected_spread —
-- measured 2026-09-29: 67 of 67 identical, and projected_total ==
-- sp_plus_pred_total on the same 67. Two field pairs, one computation.
--
-- A note added 2026-09-20 asserted the opposite for the spread — "the SPREAD
-- columns are NOT aliases ... genuinely differs (mean |diff| ~7 pts)". That
-- number is real, but it is the diff between the EPA spread and SP+ (measured
-- today: mean 7.98, median 6.83, 0/67 identical). Whoever measured it measured
-- the value the code INTENDS to write. On the strength of that note, the
-- 2026-09-20 duplicate-lens cleanup fixed the TOTAL branch of
-- ncaaf_sharp_fade_rules.rule_models_oppose_sharp and deliberately left the
-- SPREAD branch alone — so that rule kept emitting STRONG fades reasoned
-- "Both matchup, sp_plus oppose sharp" from one model counted twice, for nine
-- more days. A comment asserting a measurement is how.
--
-- WHY A NEW COLUMN AND NOT A REWRITE
-- ----------------------------------
-- sp_plus_pred_spread is NOT dishonest — when SP+ drives the primary it
-- really is the SP+ spread; it just equals projected_spread because they are
-- the same calculation. The app relies on that: GameDetailV2's Model Consensus
-- drops its SP+ tile only when the two values match (`_spDupe`), and nulling
-- the column would make that guard fail open and render an empty tile. So the
-- alias stays and is documented as one. The fix is that a REAL second spread
-- lens now exists under a name that says what it is.
--
-- NOT WIRED INTO ANY PICK PATH
-- ----------------------------
-- epa_pred_spread is written and graded only. It is visibly compressed —
-- Indiana@Rutgers EPA -0.18 against a market of 24.5, Memphis@Charlotte +0.91
-- against SP+ -22.36 — consistent with project_sp_plus_compression_927 (rolling
-- EPA is not opponent-adjusted). Nothing that selects or tiers a pick reads it
-- until it has been graded against results. Storing it is what makes that
-- grading possible; it has never been stored before.
--
-- projected_spread_source records which branch produced the primary, so a
-- consumer can tell whether epa_pred_spread is a second opinion (source
-- 'sp_plus') or a copy of the primary (source 'epa', when SP+ was missing).
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS epa_pred_spread NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS projected_spread_source TEXT;

COMMENT ON COLUMN public.ncaaf_game_context.epa_pred_spread IS
  'EPA-derived predicted HOME margin (positive = home favored), '
  '(net_epa_home - net_epa_away) * K_PTS_EPA + HFA. Genuinely independent of '
  'projected_spread when projected_spread_source = ''sp_plus'' (measured '
  '2026-09-29: 0/67 identical, mean |diff| 7.98). UNGRADED and compressed — '
  'do not use it to select or tier a pick. See 20260929b.';

COMMENT ON COLUMN public.ncaaf_game_context.projected_spread_source IS
  '''sp_plus'' or ''epa'' — which branch produced projected_spread. When '
  '''epa'', epa_pred_spread is a copy of the primary, not a second opinion.';

COMMENT ON COLUMN public.ncaaf_game_context.sp_plus_pred_spread IS
  'DISPLAY ALIAS of projected_spread whenever projected_spread_source = '
  '''sp_plus'' (67/67 identical, 2026-09-29). NOT an independent lens — never '
  'count it alongside projected_spread as corroboration. Same status as '
  'sp_plus_pred_total. For a real second spread opinion use '
  'mc_probabilities.mc_expected_margin (validated) or epa_pred_spread '
  '(ungraded). See 20260929b.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY after the next ncaaf_game_context rebuild — expect identical=0 and
-- mean_abs_diff around 7-8. identical = row count means the writer regressed
-- to copying the primary again.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT projected_spread_source,
--        count(*)                                                      AS n,
--        count(*) FILTER (WHERE abs(epa_pred_spread - projected_spread) < 0.011) AS identical,
--        round(avg(abs(epa_pred_spread - projected_spread)), 2)         AS mean_abs_diff
--   FROM public.ncaaf_game_context
--  WHERE game_date >= current_date AND epa_pred_spread IS NOT NULL
--  GROUP BY 1;
