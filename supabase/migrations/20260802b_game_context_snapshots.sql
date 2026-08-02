-- 2026-08-02b · Game context nightly snapshots (Option B).
--
-- mlb_game_context is a rolling window (~200 recent rows). Every night
-- we run a snapshot job to copy the current state into
-- mlb_game_context_snapshots — write-once-per-(game_id, snapshot_date)
-- so historical model predictions (mc_probabilities, jerry_pred_*,
-- model_pred_*) are preserved indefinitely.
--
-- Backtest engine reads from this table instead of the live rolling
-- window. Companion to Option A (compute_model_track_records mining
-- jerry_cache) — B gives us cleaner per-model separation going forward.
--
-- 30 days from now: full per-model track records at ~450 games each.

CREATE TABLE IF NOT EXISTS mlb_game_context_snapshots (
    id                  bigserial   PRIMARY KEY,
    game_id             text        NOT NULL,
    game_date           date        NOT NULL,
    snapshot_date       date        NOT NULL,       -- when we captured it (usually game_date + 1)
    home_team           text,
    away_team           text,

    -- Model predictions (preserved)
    mc_probabilities    jsonb,
    jerry_pred_home_runs numeric,
    jerry_pred_away_runs numeric,
    jerry_pred_total    numeric,
    jerry_pred_spread   numeric,
    model_pred_home_runs numeric,
    model_pred_away_runs numeric,
    model_pred_total    numeric,
    model_pred_spread   numeric,
    panel_implied_total numeric,
    panel_implied_margin numeric,

    -- Signal aggregates
    signal_confluence_net    integer,
    signal_confluence_v2_net integer,
    align_status         jsonb,
    oddscrowd_snapshot   jsonb,

    -- Primary play resolution
    primary_play        jsonb,

    -- Market state at snapshot
    home_ml_close       integer,
    away_ml_close       integer,
    close_spread        numeric,
    close_total         numeric,

    captured_at         timestamptz NOT NULL DEFAULT now(),

    UNIQUE (game_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_context_snapshots_lookup
    ON mlb_game_context_snapshots (game_date, game_id);

ALTER TABLE mlb_game_context_snapshots DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
