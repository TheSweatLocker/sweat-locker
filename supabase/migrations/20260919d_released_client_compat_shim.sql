-- 2026-09-19  COMPATIBILITY SHIM FOR ALREADY-RELEASED CLIENTS
--
-- READ THIS BEFORE "CLEANING UP" ANY COLUMN BELOW.
--
-- These columns exist for one reason: builds that are ALREADY IN USERS'
-- HANDS ask for them by name. They are not used by the pipeline and the
-- current app no longer selects them.
--
-- BACKGROUND
-- PostgREST rejects an ENTIRE select when one named column is missing
-- (42703 -> HTTP 400). There is no partial success. Commit ef697869
-- (2026-09-13) replaced `SELECT *` on mlb_game_context with a hand-typed
-- column list containing three names that do not exist:
--     supplementary_play, home_era, away_era
-- Every MLB context fetch has returned 400 since, so the client's context
-- map stayed empty and EVERY MLB game detail rendered with no MARKET row,
-- no Model Consensus, no Predicted Score, no Line Movement, no Stat
-- Projections and no Money Flow -- and MLB game cards fell back to the
-- client-side sweat tier, which diverges from the server tier by 8-12
-- points (see app/index.tsx comment at the sweat-score override).
--
-- It shipped and is live. The app-side fix (0a53e22d) removes the bogus
-- names, but that only helps people who install a FUTURE build, and there
-- is no OTA channel configured (expo-updates is not installed).
--
-- Adding the columns makes the released binary's query succeed instead.
-- The app only needs the request to return 200; these three values are
-- never read:
--   * home_era / away_era  -- nothing reads ctx.home_era. Real per-pitcher
--       ERA lives in home_pitcher_home_era / away_pitcher_away_era, which
--       the released client already selects separately.
--   * supplementary_play   -- read once into picksIdx.supplementary, which
--       every consumer already tolerates as undefined (it was undefined
--       for NFL/NCAAF by design).
-- So NULL is the correct value. They are deliberately plain nullable
-- columns, NOT generated: a generated column raises 428C9 if any writer
-- ever supplies it, and mlb_game_context is on the pipeline's hot upsert
-- path that was only just unblocked today (20260919b).
--
-- RETENTION: keep until telemetry shows no meaningful traffic from builds
-- at or below the version that shipped 2026-09-19. Dropping them sooner
-- re-breaks every MLB game detail on those installs.

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS supplementary_play jsonb,
  ADD COLUMN IF NOT EXISTS home_era           numeric,
  ADD COLUMN IF NOT EXISTS away_era           numeric;

COMMENT ON COLUMN public.mlb_game_context.supplementary_play IS
  'Compat shim for builds <= 2026-09-19 (see 20260919d). Intentionally '
  'always NULL. Not written by the pipeline; do not start populating it '
  'without checking who reads it.';
COMMENT ON COLUMN public.mlb_game_context.home_era IS
  'Compat shim for builds <= 2026-09-19 (see 20260919d). Intentionally '
  'always NULL. Real value: home_pitcher_home_era.';
COMMENT ON COLUMN public.mlb_game_context.away_era IS
  'Compat shim for builds <= 2026-09-19 (see 20260919d). Intentionally '
  'always NULL. Real value: away_pitcher_away_era.';

-- Same class of bug, same released-client problem.
-- app/index.tsx selected external_source_track_record.n_graded, which does
-- not exist -- so that fetch has ALSO been 400ing, since the 2026-09-11
-- fix that removed `market` from it for the identical reason. Consequence:
-- external-source hit rates render with no sample size behind them, and n
-- is what gates whether a percentage should be shown at all.
--
-- GENERATED here, unlike above, and the trade-off is deliberate: it makes
-- old clients receive a CORRECT number rather than a NULL, and nothing
-- writes this column today (it never existed). If a future writer starts
-- supplying n_graded it will raise 428C9 -- that is the signal to drop the
-- generated clause, not to force the insert.
--
-- Settled picks only (wins + losses + pushes), NOT n_picks, which counts
-- ungraded rows and would inflate the sample behind every hit rate.
ALTER TABLE public.external_source_track_record
  ADD COLUMN IF NOT EXISTS n_graded integer
    GENERATED ALWAYS AS (
      COALESCE(n_wins, 0) + COALESCE(n_losses, 0) + COALESCE(n_pushes, 0)
    ) STORED;

COMMENT ON COLUMN public.external_source_track_record.n_graded IS
  'Settled picks = wins+losses+pushes. Added 2026-09-19 so released '
  'builds selecting this name stop 42703-ing. Deliberately excludes '
  'ungraded picks counted by n_picks.';

NOTIFY pgrst, 'reload schema';
