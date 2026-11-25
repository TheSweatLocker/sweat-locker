-- 20261124a · SOR + SOS on the game rows for the OTHER FOUR SPORTS
--
-- ANDY 2026-11-24: "SOS should be tracked for every sport."
--
-- It IS tracked for every sport. compute_margin_strength.py runs nightly in
-- .github/scripts/nightly_cross_sport.sh over `for _sp in NFL NCAAF NHL NBA
-- NCAAB MLB` and writes stat_keys sor / sos / sor_margin / sos_margin per
-- TEAM into team_computed_stats. Verified live on 2026-11-24:
--
--     NFL 32 teams · NCAAF 137 · NBA 30 · NCAAB 366 · NHL 32 · MLB 30
--
-- What is NOT tracked per sport is the JOIN onto the game row. 20261010a
-- added these columns to ncaaf_game_context and nfl_game_context only, and
-- populate_game_sor.py is still hardcoded to `choices=('NCAAF', 'NFL')`. So
-- on nba / ncaab / nhl / mlb_game_context there is no sor/sos column AT ALL,
-- and the engine cannot reference a number we compute nightly for all six.
--
-- That is the same gap 20261010a closed for football, left open for the other
-- four sports for six weeks. The ratings were never the missing part.
--
-- WHY IT MATTERS NOW, measured 2026-11-24 from public_receipts at the
-- published price. SOR's information is concentrated in CLOSE games and
-- decays monotonically with spread size (test_sor_conditional, NCAAF):
--
--     spread  0-3   +4.39pp vs stratified expectation  (n=396)
--     spread  3-7   +1.94pp                            (n=741)
--     spread  7-14  +0.73pp                            (n=747)
--     spread 14+    +0.23pp                            (n=722)
--
-- And the coin-flip band is exactly where the app loses money:
--
--     price -119..+99   n=3204   -119.59u   ROI -3.7%
--     price -120..-199  n=1313    -69.52u   ROI -5.3%
--     price -200..-299  n= 250     +4.80u   ROI +1.9%   <- favourites are fine
--     price -300..-599  n=  66     +3.02u   ROI +4.6%
--
-- The one metric with measured signal in the band where we bleed is the one
-- the engine cannot see in four of six sports.
--
-- WHAT THIS DOES NOT DO. It creates no signal and changes no pick. It makes
-- the numbers addressable so a gate CAN be expressed and graded forward.
-- Building the gate is a separate, measured decision -- an unmeasured signal
-- collects the 0.50 RAMP_UP_PRIOR it has not earned.
--
-- THE LEAK RULE still binds the writer, not this file: team_computed_stats is
-- a CURRENT snapshot, so populate_game_sor.py writes pre-game rows only
-- (game_date >= today) and has no --all-dates flag. Stamping today's SOR onto
-- a played game would poison every future backtest reading these columns and
-- is unrecoverable, because the correct pre-game value is gone
-- (project_rolling_stats_leak_trap_929).
--
-- Types match nfl_game_context exactly (20261010a lines 74-82), so one code
-- path can write all six sports.
--
-- No NOTIFY pgrst: it makes Andy's SQL client report 42P01 relation "a", and
-- Supabase ships a DDL event trigger that reloads the schema itself
-- (feedback_notify_pgrst_breaks_andys_sql_client).

ALTER TABLE public.nba_game_context
  ADD COLUMN IF NOT EXISTS home_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS home_sos_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sos_rank  INTEGER;

ALTER TABLE public.ncaab_game_context
  ADD COLUMN IF NOT EXISTS home_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS home_sos_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sos_rank  INTEGER;

ALTER TABLE public.nhl_game_context
  ADD COLUMN IF NOT EXISTS home_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS home_sos_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sos_rank  INTEGER;

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS home_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sor       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS away_sos       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS home_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sor_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS home_sos_rank  INTEGER,
  ADD COLUMN IF NOT EXISTS away_sos_rank  INTEGER;

COMMENT ON COLUMN public.nba_game_context.home_sor IS
  'Strength of Record (margin SRS) for the home team, copied from '
  'team_computed_stats at pick time by populate_game_sor.py. Pre-game rows '
  'only -- the source is a current snapshot, so a played game must never be '
  'backfilled here. 20261124a.';

COMMENT ON COLUMN public.nba_game_context.home_sos IS
  'Strength of Schedule (margin SRS, average opponent rating) for the home '
  'team, copied from team_computed_stats by populate_game_sor.py. Higher = '
  'tougher schedule. 20261124a.';

COMMENT ON COLUMN public.ncaab_game_context.home_sor IS
  'Strength of Record (margin SRS) for the home team, written by '
  'populate_game_sor.py, pre-game rows only. 20261124a.';

COMMENT ON COLUMN public.ncaab_game_context.home_sos IS
  'Strength of Schedule (margin SRS, average opponent rating), home team. '
  'Higher = tougher schedule. NCAAB also has ncaab_team_stats.sos from the '
  'ratings feed; THIS column is the margin-SRS form computed in-house by '
  'compute_margin_strength, and the two are not interchangeable. 20261124a.';

COMMENT ON COLUMN public.nhl_game_context.home_sor IS
  'Strength of Record (margin SRS) for the home team, written by '
  'populate_game_sor.py, pre-game rows only. NOTE: app/index.tsx:8382 '
  'instructs the read generator NOT to cite SOS or SOR for NHL; storing the '
  'number does not change that display rule. 20261124a.';

COMMENT ON COLUMN public.nhl_game_context.home_sos IS
  'Strength of Schedule (margin SRS, average opponent rating), home team. '
  'Higher = tougher schedule. 20261124a.';

COMMENT ON COLUMN public.mlb_game_context.home_sor IS
  'Strength of Record (margin SRS) for the home team, written by '
  'populate_game_sor.py, pre-game rows only. 20261124a.';

COMMENT ON COLUMN public.mlb_game_context.home_sos IS
  'Strength of Schedule (margin SRS, average opponent rating), home team. '
  'Higher = tougher schedule. 20261124a.';
