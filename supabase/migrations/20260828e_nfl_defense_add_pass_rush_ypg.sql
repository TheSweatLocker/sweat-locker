-- Add missing per-facet YPG columns to nfl_team_defense_stats (2026-08-28).
--
-- The 20260828c initial CREATE had def_pass_ypg + def_rush_ypg in the
-- schema definition but the applied version dropped them (paste truncation
-- or older draft applied). Backfill was 400ing on the missing columns.
-- ADD IF NOT EXISTS makes this safe if 20260828c was ever fully applied.
--
-- Also normalize season_type default to match nfl_team_stats convention:
-- upstream nflverse pull uses 'REG'/'PRE' uppercase.

ALTER TABLE nfl_team_defense_stats
    ADD COLUMN IF NOT EXISTS def_pass_ypg NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS def_rush_ypg NUMERIC(6, 2);

-- Fix existing rows that were written with lowercase 'reg' (case
-- mismatch bug — nfl_team_stats stores 'REG' so the backfill's
-- upstream join returned 0 rows and def_ypg came out as 0).
UPDATE nfl_team_defense_stats
   SET season_type = 'REG'
 WHERE season_type = 'reg';

-- Change the default for future writes.
ALTER TABLE nfl_team_defense_stats
    ALTER COLUMN season_type SET DEFAULT 'REG';

NOTIFY pgrst, 'reload schema';
