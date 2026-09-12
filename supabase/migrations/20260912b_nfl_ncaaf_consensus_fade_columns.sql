-- 2026-09-12: NFL + NCAAF consensus_fade columns (parity with MLB)
--
-- Andy audit tonight: NFL + NCAAF game_context have ONLY 1 sharp-signal
-- column (oddscrowd_snapshot, NULL for NFL). MLB has 6:
--   consensus_fade_flag, consensus_fade_pct, consensus_fade_side,
--   consensus_fade_note, consensus_fade_n, oddscrowd_snapshot.
--
-- Root cause: detect_consensus_fade.py was written 2026-07 targeting
-- MLB only, later extended to NFL + NCAAB in SPORT_CONTEXT_TABLE map
-- but the NFL columns were never migrated. NCAAF isn't in the map at
-- all — omitted per typical MLB-first pattern.
--
-- Fix: add the 5 fade columns to both tables so detect_consensus_fade
-- can run --sport NFL and --sport NCAAF cleanly. Also add NCAAF to
-- the script's sport map (see companion commit).
--
-- Impact: Sunday's NFL slate + Saturday's NCAAF slate will get the
-- same consensus-fade alert treatment MLB has. When 75%+ of external
-- sources are on one side AND the historical bucket has hit <48% at
-- n>=20, `consensus_fade_flag=TRUE` fires. Downstream ensemble +
-- Jerry read composers already read these fields (via MLB precedent);
-- NFL/NCAAF pipelines will inherit the signal automatically.

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS consensus_fade_flag BOOLEAN,
  ADD COLUMN IF NOT EXISTS consensus_fade_pct  NUMERIC,
  ADD COLUMN IF NOT EXISTS consensus_fade_side TEXT,
  ADD COLUMN IF NOT EXISTS consensus_fade_note TEXT,
  ADD COLUMN IF NOT EXISTS consensus_fade_n    INTEGER;

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS consensus_fade_flag BOOLEAN,
  ADD COLUMN IF NOT EXISTS consensus_fade_pct  NUMERIC,
  ADD COLUMN IF NOT EXISTS consensus_fade_side TEXT,
  ADD COLUMN IF NOT EXISTS consensus_fade_note TEXT,
  ADD COLUMN IF NOT EXISTS consensus_fade_n    INTEGER;

NOTIFY pgrst, 'reload schema';
