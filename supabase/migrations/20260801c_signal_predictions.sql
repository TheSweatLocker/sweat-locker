-- 2026-08-01c · Signal predictions log (the tracking framework).
--
-- One row per (signal_source, game, market) — the atomic prediction unit.
-- Every model output, every external pick, every money-flow snapshot,
-- every cohort firing gets logged here nightly. Post-game grading fills
-- result. Aggregations feed signal_track_records for rolling W/L rates
-- that Jerry synth cites and the app receipts tab renders.
--
-- Sport-universal. MLB backfills first from mlb_pipeline_props +
-- mlb_game_context history; extend to NBA/NFL/NCAAF/NCAAB/UFC as their
-- pipelines land.

CREATE TABLE IF NOT EXISTS signal_predictions (
    id              bigserial   PRIMARY KEY,

    sport           text        NOT NULL,      -- MLB / NBA / NFL / NCAAF / NCAAB / UFC
    game_date       date        NOT NULL,
    game_id         text        NOT NULL,
    market          text        NOT NULL,      -- ML / RL / SPREAD / TOTAL / PROP / FIGHT

    signal_source   text        NOT NULL,      -- MC / PANEL / V3 / V4 / JERRY /
                                                -- OC_MONEY / OC_BETS / ACTION_PUBLIC /
                                                -- BETFIRM / DOC_SPORTS / SBR / PICKSWISE /
                                                -- COVERS / COHORT:<name> / etc.
    signal_side     text,                       -- HOME/AWAY/OVER/UNDER/BACK/FADE/side_A/side_B
    signal_line     numeric,                    -- optional — for totals/spreads/props
    signal_conf     numeric,                    -- 0-100 or 0-1 depending on source

    actual_result   text,                       -- W / L / PUSH / NO_ACTION / null=pending
    actual_value    numeric,                    -- margin / total / prop actual — for edge audit
    resolved_at     timestamptz,

    metadata        jsonb,                      -- raw fields for audit
    logged_at       timestamptz NOT NULL DEFAULT now(),

    UNIQUE (sport, game_date, game_id, market, signal_source, signal_side)
);

CREATE INDEX IF NOT EXISTS idx_signal_predictions_lookup
    ON signal_predictions (sport, game_date, signal_source);
CREATE INDEX IF NOT EXISTS idx_signal_predictions_pending
    ON signal_predictions (sport, game_date)
    WHERE actual_result IS NULL;

ALTER TABLE signal_predictions DISABLE ROW LEVEL SECURITY;


-- Rolling aggregate: one row per (sport, signal_source, market, direction, window)
CREATE TABLE IF NOT EXISTS signal_track_records (
    id              bigserial   PRIMARY KEY,

    sport           text        NOT NULL,
    signal_source   text        NOT NULL,
    market          text        NOT NULL,
    direction_filter text,                      -- optional: 'HOME_ONLY', 'FAV_ONLY', etc.
    bucket_window   text        NOT NULL,       -- 'lifetime' | '90d' | '30d' | '14d' | '7d'

    wins            integer     DEFAULT 0,
    losses          integer     DEFAULT 0,
    pushes          integer     DEFAULT 0,
    hit_rate        numeric,                    -- wins / (wins + losses)
    roi_est         numeric,                    -- rough EV assuming -110 or provided odds
    sample_n        integer,

    computed_at     timestamptz NOT NULL DEFAULT now(),

    UNIQUE (sport, signal_source, market, direction_filter, bucket_window)
);

CREATE INDEX IF NOT EXISTS idx_signal_track_records_lookup
    ON signal_track_records (sport, market, bucket_window);

ALTER TABLE signal_track_records DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
