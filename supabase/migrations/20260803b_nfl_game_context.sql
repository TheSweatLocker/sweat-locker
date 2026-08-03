-- 2026-08-03b · NFL game context table (Sprint 2 Day 1).
--
-- Companion to nfl_pipeline_props (player-level) — this stores game-level
-- context: weather, injuries, pace, market lines. Feeds the NFL prop
-- projection layer's environmental multipliers (weather_mult, pace_mult)
-- and Jerry synthesis for richer prose ("15mph wind, top WR out, etc.").
--
-- Analogous to mlb_game_context but NFL-specific (no starter pitcher
-- concept, no bullpen depth, but has weather + injuries + pace).
--
-- Populator lives at fetch_nfl_game_context.py (build in Sprint 2b using
-- nfl_data_py schedules + injuries + weather scrape).

CREATE TABLE IF NOT EXISTS nfl_game_context (
    id                  bigserial   PRIMARY KEY,
    game_id             text        NOT NULL UNIQUE,   -- matches nfl_pipeline_props.game_id
    game_date           date        NOT NULL,
    week                integer,
    season_phase        text,                          -- 'preseason' | 'regular' | 'postseason'

    home_team           text,
    away_team           text,
    home_team_abbr      text,
    away_team_abbr      text,
    kickoff_time        timestamptz,

    -- Market
    home_ml_close       integer,
    away_ml_close       integer,
    close_spread        numeric,
    close_total         numeric,
    home_ml_open        integer,
    away_ml_open        integer,
    open_spread         numeric,
    open_total          numeric,

    -- Weather (outdoor games)
    venue               text,
    is_dome             boolean,
    temperature_f       numeric,
    wind_speed_mph      numeric,
    wind_direction      text,
    precipitation_pct   numeric,

    -- Pace + strength context (aggregate from nfl_data_py L5)
    home_plays_per_gm   numeric,
    away_plays_per_gm   numeric,
    home_off_epa_l5     numeric,
    away_off_epa_l5     numeric,
    home_def_epa_l5     numeric,
    away_def_epa_l5     numeric,

    -- Rest / travel
    home_rest_days      integer,                       -- days since last game
    away_rest_days      integer,
    away_travel_miles   integer,                       -- coast-to-coast etc.

    -- Injury status snapshots (JSONB per-team)
    home_injuries       jsonb,                         -- [{name, position, status}]
    away_injuries       jsonb,
    home_qb             text,                          -- projected starting QB
    away_qb             text,
    top_wr_status       jsonb,                         -- {home: 'active', away: 'questionable'}

    -- Aggregate signals (populated by NFL cohort engine when it ships)
    signal_confluence_net integer,
    align_status        jsonb,
    oddscrowd_snapshot  jsonb,
    mc_probabilities    jsonb,                         -- Monte Carlo sim outputs (Sprint 3+)
    primary_play        jsonb,                         -- resolver output

    -- Predictions (per-model, mirroring MLB structure)
    jerry_pred_total    numeric,
    jerry_pred_spread   numeric,
    model_pred_total    numeric,
    model_pred_spread   numeric,
    panel_implied_total numeric,
    panel_implied_margin numeric,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nfl_context_date_week
    ON nfl_game_context (game_date, week);

ALTER TABLE nfl_game_context DISABLE ROW LEVEL SECURITY;

COMMENT ON TABLE nfl_game_context IS
    'NFL game-level context — weather, injuries, pace, market. Companion to nfl_pipeline_props (player-level). Populated by fetch_nfl_game_context.py (Sprint 2b).';

NOTIFY pgrst, 'reload schema';
