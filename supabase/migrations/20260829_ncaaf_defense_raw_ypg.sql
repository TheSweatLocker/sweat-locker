-- Extend ncaaf_team_defense_stats with raw pass/rush YPG allowed (2026-08-29).
--
-- Prior migration (20260828d) shipped def_ppg + EPA-based allowed. But the
-- NCAAF card users see wants RAW yards allowed (pass_yds_allowed and
-- rush_yds_allowed) — the numbers a bettor scans. Adding both, populated
-- by ncaaf_team_defense_backfill.py from opponents' seasonal offense pull.
--
-- Also backfill ncaaf_team_stats.games (NULL from CFBD's advanced-stats
-- endpoint which doesn't return games count). Uses regular-season default
-- of 12 for prior seasons; downstream per-game math needs this to work.

ALTER TABLE ncaaf_team_defense_stats
    ADD COLUMN IF NOT EXISTS def_pass_ypg NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS def_rush_ypg NUMERIC(6, 2);

-- Backfill games=12 for 2022-2024 (typical FBS regular season) where NULL.
-- 2025 also 12-13 depending on conference/bowl; use 12 as conservative
-- baseline. Postseason bowls handled separately.
UPDATE ncaaf_team_stats
   SET games = 12
 WHERE games IS NULL
   AND season IN (2022, 2023, 2024, 2025)
   AND (sp_overall IS NOT NULL OR off_epa_per_play IS NOT NULL);

NOTIFY pgrst, 'reload schema';
