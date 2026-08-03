-- 2026-08-03 · NFL prop pipeline table (Sprint 1 Day 1).
--
-- Mirrors mlb_pipeline_props schema exactly so grade_prop_jerry_reads,
-- compute_prop_bucket_roi, and generate_prop_jerry_synthesis can all be
-- wired via the sport-parametric PROPS_TABLE registry without special-
-- casing NFL. Adds NFL-specific fields (position, week, opp_team,
-- home_away) that MLB doesn't have.
--
-- prop_type convention (per fix 2026-08-02 for MLB):
--   Always full form: pass_yds_over, pass_yds_under, pass_tds_over,
--   rush_yds_over, rec_yds_under, anytime_td_over, etc.
--   Never bare family — matches the sweeper/generator/grader contract.
--
-- Naming convention (Big 4 markets for Week 1 launch):
--   pass_yds_{over,under}     — QB passing yards
--   pass_tds_{over,under}     — QB passing TDs
--   ints_{over,under}         — QB interceptions
--   pass_attempts_{over,under} — QB attempts
--   rush_yds_{over,under}     — RB/QB rushing yards
--   rec_yds_{over,under}      — WR/TE receiving yards
--   receptions_{over,under}   — WR/TE receptions
--   anytime_td_{over,under}   — any position, TD scored yes/no
--
-- 'over'/'under' direction convention preserved (MLB parity).
-- anytime_td uses 'over' = yes, 'under' = no for schema consistency.

CREATE TABLE IF NOT EXISTS nfl_pipeline_props (
    id                bigserial   PRIMARY KEY,
    game_date         date        NOT NULL,
    game_id           text        NOT NULL,
    week              integer,                          -- NFL week num, null for preseason
    season_phase      text,                             -- 'preseason' | 'regular' | 'postseason'

    -- Player
    player_name       text        NOT NULL,
    player_team       text,
    position          text,                             -- QB | RB | WR | TE
    opp_team          text,                             -- opposing defense (for cohort lookups)
    home_away         text,                             -- 'HOME' | 'AWAY'

    matchup           text,

    -- Prop
    prop_type         text        NOT NULL,             -- pass_yds_over, rec_yds_under, etc.
    prop_line         numeric     NOT NULL,
    direction         text        NOT NULL,             -- 'over' | 'under'

    -- Scoring
    conviction        integer     NOT NULL DEFAULT 0,   -- 0-100
    tier              text,                             -- PRIME | STRONG | LEAN | SKIP | COVERAGE

    -- Signals (JSONB — same shape as MLB, sport-specific keys)
    signals           jsonb,

    -- Book data
    book_line         numeric,
    book_over_odds    integer,
    book_under_odds   integer,
    book_source       text,

    -- Projection layer (transparent multipliers, per 2026-08-03 spec)
    projection        jsonb,                            -- {value, inputs: {L5_avg, opp_D_mult, ...}}
    consensus         jsonb,                            -- ESPN + FantasyPros consensus (validator)
    consensus_delta   numeric,                          -- |our_proj - consensus| / consensus

    -- Refit conviction (post-launch calibration)
    refit_conviction  integer,
    refit_version     text,

    -- Lifecycle
    lineup_state      text,                             -- 'confirmed' | 'pending' | 'coverage_stub'
    stack_alert       boolean     DEFAULT false,
    last_attached_at  timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),

    -- Grading
    result            text,                             -- Win | Loss | Push | NO_ACTION | UNGRADEABLE
    final_value       numeric,                          -- actual stat value (e.g. 287.5 pass yds)
    resolved_at       timestamptz,

    -- Enforce natural key uniqueness so sweeper dedup works
    UNIQUE (game_id, player_name, prop_type, direction)
);

CREATE INDEX IF NOT EXISTS idx_nfl_props_date_tier
    ON nfl_pipeline_props (game_date, tier);
CREATE INDEX IF NOT EXISTS idx_nfl_props_player
    ON nfl_pipeline_props (player_name, game_date);
CREATE INDEX IF NOT EXISTS idx_nfl_props_pending_result
    ON nfl_pipeline_props (game_date, result) WHERE result IS NULL;

ALTER TABLE nfl_pipeline_props DISABLE ROW LEVEL SECURITY;

COMMENT ON TABLE nfl_pipeline_props IS
    'NFL prop pipeline — mirrors mlb_pipeline_props with position/week/opp added. Sprint 1 launch Aug 3 2026, first data preseason W2 (Aug 14-18).';

NOTIFY pgrst, 'reload schema';
