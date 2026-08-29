-- Extend ncaaf_team_stats with volumetric + discipline stats (2026-08-28).
--
-- CFBD's /stats/season/advanced (already pulled by ncaaf_stats_pull.py)
-- returns EPA-only. Their /stats/season (non-advanced) has raw yards,
-- penalties, downs, TOP, turnovers. Adding those columns here so we
-- can enrich ctx with the volumetric stats the game-detail card needs.

ALTER TABLE ncaaf_team_stats
    -- Offense volume
    ADD COLUMN IF NOT EXISTS pass_yards          INTEGER,
    ADD COLUMN IF NOT EXISTS pass_tds            INTEGER,
    ADD COLUMN IF NOT EXISTS pass_completions    INTEGER,
    ADD COLUMN IF NOT EXISTS pass_attempts       INTEGER,
    ADD COLUMN IF NOT EXISTS pass_ints           INTEGER,
    ADD COLUMN IF NOT EXISTS rush_yards          INTEGER,
    ADD COLUMN IF NOT EXISTS rush_tds            INTEGER,
    ADD COLUMN IF NOT EXISTS rush_attempts       INTEGER,
    -- Situational
    ADD COLUMN IF NOT EXISTS first_downs         INTEGER,
    ADD COLUMN IF NOT EXISTS third_down_conv     INTEGER,
    ADD COLUMN IF NOT EXISTS third_downs         INTEGER,
    ADD COLUMN IF NOT EXISTS fourth_down_conv    INTEGER,
    ADD COLUMN IF NOT EXISTS fourth_downs        INTEGER,
    -- Discipline
    ADD COLUMN IF NOT EXISTS penalties           INTEGER,
    ADD COLUMN IF NOT EXISTS penalty_yards       INTEGER,
    -- Ball security
    ADD COLUMN IF NOT EXISTS turnovers           INTEGER,
    ADD COLUMN IF NOT EXISTS fumbles_lost        INTEGER,
    -- Time
    ADD COLUMN IF NOT EXISTS possession_time_sec INTEGER,
    -- Defense events (own defense records)
    ADD COLUMN IF NOT EXISTS def_sacks           INTEGER,
    ADD COLUMN IF NOT EXISTS def_ints            INTEGER,
    ADD COLUMN IF NOT EXISTS def_fumbles_rec     INTEGER;

NOTIFY pgrst, 'reload schema';
