-- 2026-08-02 · Model track records (adaptive ensemble workstream Phase 1a).
--
-- Rolling per-model accuracy across configurable time windows so Jerry
-- synth can weight signals by recent performance instead of treating
-- them equally. Populated nightly by compute_model_track_records.py
-- which mines jerry_cache historical struct + mlb_game_results.
--
-- Companion to signal_track_records (2026-08-01c) — same shape but
-- for MODELS (MC, PANEL, V4, etc.) not signals. Named separately for
-- clarity and because model_name resolution differs from signal_source.
--
-- Vision: [[project_adaptive_model_ensemble_802]]

CREATE TABLE IF NOT EXISTS model_track_records (
    id              bigserial   PRIMARY KEY,

    sport           text        NOT NULL,       -- MLB / NBA / NFL / etc.
    model_name      text        NOT NULL,       -- MC / PANEL / V4 / RESOLVER / MODEL_TOTAL / etc.
    market          text        NOT NULL,       -- ML / SPREAD / TOTAL / RL
    direction_filter text,                       -- optional: 'HOME_ONLY', 'FAV_ONLY', 'OVER_ONLY'
    bucket_window   text        NOT NULL,       -- 'lifetime' | '90d' | '30d' | '14d' | '7d'

    wins            integer     NOT NULL DEFAULT 0,
    losses          integer     NOT NULL DEFAULT 0,
    pushes          integer     NOT NULL DEFAULT 0,
    sample_n        integer     NOT NULL DEFAULT 0,
    hit_rate        numeric,                     -- wins / (wins + losses)
    roi_pct         numeric,                     -- juice-adjusted estimate at avg -110
    avg_edge_pts    numeric,                     -- avg |predicted - actual| in the model's native unit

    -- Recommended weight (0.0-3.0) derived from hit_rate vs baseline. Computed
    -- at recompute time. Jerry can consult this directly OR use raw hit_rate
    -- for its own weighting math. Nullable — weight generation is Phase 2.
    recommended_weight numeric,

    computed_at     timestamptz NOT NULL DEFAULT now(),

    UNIQUE (sport, model_name, market, direction_filter, bucket_window)
);

CREATE INDEX IF NOT EXISTS idx_model_track_records_lookup
    ON model_track_records (sport, market, bucket_window);

ALTER TABLE model_track_records DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
