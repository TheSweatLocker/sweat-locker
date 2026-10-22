-- == 2026-10-21 - SIX PROP SIGNALS TESTING FIELDS THAT DO NOT EXIST ==
--
-- B121. Of 25 enabled NFL prop signals, 12 never fired on a single graded
-- row. Nine of those were my own measurement error (the calibrator ran with
-- an empty ctx; fixed). The rest are real: the signal expression names a key
-- that is not in the signals JSON, and `.get()` on a missing key returns
-- NULL, so the condition is quietly FALSE forever. No error, no log, no
-- firing - the signal simply never has an opinion and nothing says so.
--
-- Measured against the live JSON on both tables:
--
--   signal                      tests              exists?
--   nfl_edge_pct_supports       signals._edge_pct  NO - NFL writes edge_pct
--   nfl_edge_pct_strong         signals._edge_pct  NO -       "
--   nfl_edge_pct_opposes        signals._edge_pct  NO -       "
--   projection_edge_supports    _edge_at_book      NO - in NEITHER sport
--   projection_edge_strong      _edge_at_book      NO -       "
--   nfl_opp_matchup_favorable   signals.opp_matchup NO - not a key anywhere
--
-- projection_edge_* is defined for MLB AND NFL, so two dead signals sit on
-- MLB, the one surface that actually publishes props.
--
-- ── THE RENAME ALONE WOULD HAVE BEEN WORSE THAN THE BUG ─────────────────
-- MLB writes `_edge_pct` and NFL writes `edge_pct`, and they are the same
-- concept at 100x DIFFERENT SCALE:
--
--   MLB _edge_pct   median 0.189   range -0.6 .. 0.215    a FRACTION
--   NFL edge_pct    median 9.8     range -27.2 .. 162     PERCENTAGE POINTS
--
-- The three NFL signals carry fraction-scale thresholds (0.10 / 0.25 /
-- -0.15) because they were written against MLB's field. Pointing them at
-- NFL's field without rescaling fires them on percentage-point data:
--
--   >= 0.10 "supports"   302 of 370  (82%)
--   >= 0.25 "strong"     300 of 370  (81%)
--
-- Two separate tiers firing on effectively the same 81% of the board - a
-- degenerate pair that would then have earned weight through calibration,
-- exactly like nhl_prop_l10_extreme at 46.9%. Rescaled x100 into NFL's own
-- units it becomes a real ladder:
--
--   >= 10 supports   183 of 370  (49%)
--   >= 25 strong      63 of 370  (17%)
--   <= -15 opposes    13 of 370  ( 4%)
--
-- Same family as the NFL off-EPA-per-play against def-EPA-per-game pair that
-- was ~30x apart: a shared NAME is not a shared SCALE, and the fix for a
-- naming bug is not always a rename.

-- ── 1. The three NFL signals: correct field AND correct scale ───────────
UPDATE public.signal_sources
   SET condition_expr = 'isinstance(p.get("signals"), dict) and '
                        '"edge_pct" in p["signals"] and '
                        'p["signals"]["edge_pct"] is not None and '
                        'float(p["signals"]["edge_pct"]) >= 10',
       updated_at = now()
 WHERE signal_key = 'nfl_edge_pct_supports' AND sport = 'NFL';

UPDATE public.signal_sources
   SET condition_expr = 'isinstance(p.get("signals"), dict) and '
                        '"edge_pct" in p["signals"] and '
                        'p["signals"]["edge_pct"] is not None and '
                        'float(p["signals"]["edge_pct"]) >= 25',
       updated_at = now()
 WHERE signal_key = 'nfl_edge_pct_strong' AND sport = 'NFL';

UPDATE public.signal_sources
   SET condition_expr = 'isinstance(p.get("signals"), dict) and '
                        '"edge_pct" in p["signals"] and '
                        'p["signals"]["edge_pct"] is not None and '
                        'float(p["signals"]["edge_pct"]) <= -15',
       updated_at = now()
 WHERE signal_key = 'nfl_edge_pct_opposes' AND sport = 'NFL';

-- ── 2. The three with NO field to point at: DISABLED ────────────────────
-- `_edge_at_book` and `opp_matchup` are not keys in either sport's signals
-- JSON, and there is no equivalent to rename them to:
--
--   MLB signals keys include _edge_pct, _edge_pct_dir
--   NFL signals keys include edge_pct, opp_pct, opp_metric, opp_col,
--                            opp_ratio, def_top10, def_recent_stout,
--                            proj_shadow, opp_adj_live
--
-- opp_matchup was a PROSE field ("weak", "poor", "bottom" are substring
-- tested); opp_pct is a percentile and def_top10 a boolean. Rewriting the
-- condition onto those is a change of MEANING, not a rename, and which
-- threshold counts as a favourable matchup is a judgement that belongs with
-- a measurement rather than inside a migration.
--
-- Disabled rather than left enabled, because an enabled signal that can
-- never fire is worse than an absent one: it inflates the signal count a
-- reader uses to judge how many independent opinions back a pick, and it
-- makes the registry look like it covers ground it does not. Re-enable with
-- a working expression whenever the field exists or the rewrite is decided.
UPDATE public.signal_sources
   SET enabled = FALSE,
       updated_at = now(),
       description = COALESCE(description || ' | ', '') ||
                     'DISABLED 2026-10-21: tests signals._edge_at_book, '
                     'which is not a key in MLB or NFL props. Never fired. '
                     'No equivalent field to rename to.'
 WHERE signal_key IN ('projection_edge_supports', 'projection_edge_strong')
   AND subject_scope IN ('prop', 'player_prop');

UPDATE public.signal_sources
   SET enabled = FALSE,
       updated_at = now(),
       description = COALESCE(description || ' | ', '') ||
                     'DISABLED 2026-10-21: tests signals.opp_matchup, which '
                     'does not exist. It expected PROSE; the real fields are '
                     'opp_pct (percentile), def_top10 and def_recent_stout '
                     '(booleans). Rewriting onto those changes the meaning, '
                     'so it needs a measurement first.'
 WHERE signal_key = 'nfl_opp_matchup_favorable';

-- ── 3. Say what happened ────────────────────────────────────────────────
DO $$
DECLARE n_fixed INT; n_off INT;
BEGIN
  SELECT COUNT(*) INTO n_fixed FROM public.signal_sources
   WHERE signal_key IN ('nfl_edge_pct_supports', 'nfl_edge_pct_strong',
                        'nfl_edge_pct_opposes')
     AND condition_expr LIKE '%"edge_pct"%'
     AND condition_expr NOT LIKE '%_edge_pct%';
  SELECT COUNT(*) INTO n_off FROM public.signal_sources
   WHERE signal_key IN ('projection_edge_supports', 'projection_edge_strong',
                        'nfl_opp_matchup_favorable')
     AND enabled = FALSE;
  RAISE NOTICE 'repointed % NFL edge_pct signal(s) at the real field and '
               'scale; disabled % signal(s) with no field to test',
               n_fixed, n_off;
  IF n_fixed <> 3 THEN
    RAISE WARNING 'expected 3 repointed signals, got % - check the '
                  'condition_expr text matched', n_fixed;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
