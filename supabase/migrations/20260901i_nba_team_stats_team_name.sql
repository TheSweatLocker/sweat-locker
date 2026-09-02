-- nba_team_stats: add team_name + fix truncated team_abbrev (2026-09-01)
--
-- ROOT-CAUSE FIX for the NBA data hygiene issues found in the 9/1 audit:
--
--   1. nba_elo.py:140 was writing `team[:8]` as team_abbrev — truncating
--      full ESPN team names to 8 chars ("Boston Celtics" → "Boston C",
--      "Los Angeles Lakers" → "Los Ange"). Not real NBA abbreviations,
--      not useful for anything downstream.
--
--   2. No full team_name column at all — team_stats_rolling matview
--      had to bridge via a hardcoded VALUES map of the 8-char truncations
--      back to full names. Fragile + circular.
--
--   3. wins/losses were placeholders (wins=games_played, losses=0).
--
-- This migration:
--   - Adds team_name TEXT (nullable initially, populated by rewritten
--     nba_elo.py on next run).
--   - Cleans up existing truncated rows so re-population starts fresh
--     with real NBA abbrevs (BOS, LAL, LAC, etc.) from ESPN.
--   - After nba_elo.py rewrite lands (same commit), the PK stays
--     (team_abbrev, season) but abbrev values are the real 3-letter
--     ESPN codes — no more truncation.

ALTER TABLE public.nba_team_stats
  ADD COLUMN IF NOT EXISTS team_name TEXT;

-- Cleanup: any row whose team_abbrev looks like a truncated full name
-- (contains a space, or > 5 chars) is the old broken format. Delete
-- so re-population from ESPN doesn't collide on the PK.
-- Real NBA abbrevs are 2-3 characters (BOS, LAL, LAC, NYK, PHI, PHX).
DELETE FROM public.nba_team_stats
 WHERE team_abbrev IS NOT NULL
   AND (team_abbrev LIKE '% %' OR LENGTH(team_abbrev) > 5);

-- Also delete the All-Star game placeholder rows ("Team Can", "Team Chu",
-- "Team Ken", "Team Sha") which pollute rankings + are not real teams.
-- Caught by the LIKE '% %' filter above but making it explicit.
DELETE FROM public.nba_team_stats
 WHERE team_abbrev LIKE 'Team %';

NOTIFY pgrst, 'reload schema';
