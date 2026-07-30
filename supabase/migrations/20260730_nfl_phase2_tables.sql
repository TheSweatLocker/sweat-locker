-- 2026-07-30 · Phase 2 NFL support tables (starters, injuries, weather).
-- Feeds the redesigned game detail's NFL slot ([[project_game_detail_redesign_729]]).
-- All three pipes populate before Aug 7 preseason opener (CAR@ARI).

-- ─── nfl_starters ───────────────────────────────────────────────────────
-- One row per (team, week, season, position). Populated by
-- nfl_weekly_starters.py from ESPN scoreboard. Refreshed Wed + Sun.
CREATE TABLE IF NOT EXISTS nfl_starters (
    id            bigserial PRIMARY KEY,
    season        integer NOT NULL,
    week          integer NOT NULL,
    season_type   text    NOT NULL DEFAULT 'REG',    -- 'REG' | 'POST' | 'PRE'
    team          text    NOT NULL,
    position      text    NOT NULL,                  -- 'QB' | 'RB1' | 'WR1' etc.
    player_name   text    NOT NULL,
    player_id     text,                              -- nfl_data_py player_id when derivable
    is_starter    boolean NOT NULL DEFAULT true,
    source        text    DEFAULT 'espn_scoreboard',
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (season, week, season_type, team, position)
);
CREATE INDEX IF NOT EXISTS idx_nfl_starters_team_week ON nfl_starters (team, season, week);

-- ─── nfl_injuries ───────────────────────────────────────────────────────
-- One row per (team, player, week). Populated by nfl_injuries_pull.py from
-- nfl_data_py.import_injuries(). Refreshed Wed + Fri + Sun (as reports drop).
CREATE TABLE IF NOT EXISTS nfl_injuries (
    id                bigserial PRIMARY KEY,
    season            integer NOT NULL,
    week              integer NOT NULL,
    team              text    NOT NULL,
    player_name       text    NOT NULL,
    player_id         text,
    position          text,
    injury_status     text,                        -- 'Out' | 'Doubtful' | 'Questionable' | 'Full'
    practice_status   text,                        -- 'DNP' | 'Limited' | 'Full'
    body_part         text,                        -- 'Knee' | 'Ankle' | 'Illness' etc.
    report_date       date,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (season, week, team, player_name)
);
CREATE INDEX IF NOT EXISTS idx_nfl_injuries_team_week ON nfl_injuries (team, season, week);

-- ─── Weather columns on nfl_game_context (per-game snapshot) ────────────
-- Fields already exist (temp, wind) but are null — nfl_weather_pull.py
-- populates them for upcoming games via OpenWeather API keyed on venue lat/lng.
-- No schema change needed here; just noting intent.

NOTIFY pgrst, 'reload schema';
