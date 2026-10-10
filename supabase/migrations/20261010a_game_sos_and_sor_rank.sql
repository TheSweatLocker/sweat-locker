-- 20261010a · SOS + SOR/SOS RANK on the football game rows, and SOR/SOS on NFL
--
-- ANDY 2026-10-10: "The engine should know situations whrre SOR matters."
--
-- It cannot today, because the numbers never reach a game row. Traced rather
-- than assumed:
--
--   * SOR/SOS ARE computed and healthy -- stat_keys sor, sos, sor_margin,
--     sos_margin, sor_winpct, sos_winpct for 138 FBS teams, RANKED, refreshed
--     daily into team_computed_stats by compute_margin_strength.
--   * ncaaf_game_context.home_sor / away_sor existed but were NULL on 494 of
--     494 rows, because NOTHING in the repo ever wrote them (grep for writers
--     of `home_sor` returned zero hits). populate_game_sor.py now fills them
--     -- 69/69 upcoming games verified on 2026-10-10.
--   * There is NO home_sos / away_sos column at all, so SOS cannot be stored
--     per game even though we compute and rank it.
--   * nfl_game_context has NO sor/sos columns whatsoever.
--   * ZERO of 837 signal_sources rows mention sor or sos, so no SOR/SOS gate
--     can be expressed until the columns exist to reference.
--
-- WHY THE RANK COLUMNS AND NOT JUST THE RATING. SOR is a ridge-shrunk SRS
-- fit, so the raw rating is COMPRESSED: on a 28+ point line our implied margin
-- sits ~26.5 points off the market on average. A rank is scale-free and so
-- immune to that compression, which makes it a materially different feature
-- rather than a cosmetic one. (Measured: the rank form did NOT outperform the
-- points form -- test_sor_conditional, 0-7 band rank gap >=10 is +2.16pp,
-- inside noise -- so this is stored to be gradeable, not because it is already
-- known to be better.)
--
-- WHAT THIS MIGRATION DOES NOT DO. It creates no signal and changes no pick.
-- It only makes the numbers addressable. The measured state of the question,
-- for whoever builds the gate: SOR's information is real but concentrated in
-- CLOSE games and decays monotonically with spread size --
--
--     spread  0-3   +4.39pp vs stratified expectation  (n=396)
--     spread  3-7   +1.94pp                            (n=741)
--     spread  7-14  +0.73pp                            (n=747)
--     spread 14+    +0.23pp                            (n=722)
--
-- The 0-3 band is n=396 against a 2SE band of 5.0: SAMPLE TOO SMALL, which is
-- not the same as null. Any gate built on this must be graded FORWARD, and
-- must be gated to close games -- the 14+ band carries essentially nothing.
--
-- ⚠ LEAK WARNING FOR ANY BACKFILL. team_computed_stats is a CURRENT snapshot.
-- Writing it onto an already-played game stamps post-game information onto a
-- pre-game row and irreversibly poisons every backtest that later reads these
-- columns (project_rolling_stats_leak_trap_929). populate_game_sor.py refuses
-- to touch game_date < today for exactly this reason and has no --all-dates
-- flag. Historical values must be reconstructed walk-forward with fit_srs,
-- the way test_sor_conditional.py does.
--
-- Additive and idempotent: IF NOT EXISTS throughout, no backfill, no
-- rewrites, nothing dropped.

-- ── NCAAF ───────────────────────────────────────────────────────────────
ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS home_sos_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sos_rank  INTEGER;

COMMENT ON COLUMN public.ncaaf_game_context.home_sos IS
  'Strength of Schedule (margin SRS, avg opponent rating) for the home team, '
  'copied from team_computed_stats at pick time by populate_game_sor.py. '
  'PRE-GAME ONLY: never backfilled onto played games -- the source is a '
  'current snapshot. Reconstruct history with fit_srs/sos_from instead.';
COMMENT ON COLUMN public.ncaaf_game_context.home_sor_rank IS
  'Rank 1 = best. Scale-free, so unlike home_sor it is immune to the ridge '
  'compression in the SRS fit.';

-- ── NFL · had no sor/sos columns at all ─────────────────────────────────
ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS home_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS home_sos_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sos_rank  INTEGER;

COMMENT ON COLUMN public.nfl_game_context.home_sor IS
  'Strength of Record (margin SRS) for the home team, copied from '
  'team_computed_stats at pick time. NFL had no sor/sos columns before '
  '20261010a. PRE-GAME ONLY -- see the ncaaf_game_context.home_sos comment.';

NOTIFY pgrst, 'reload schema';
