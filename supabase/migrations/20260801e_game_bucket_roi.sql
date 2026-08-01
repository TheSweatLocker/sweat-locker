-- 2026-08-01e · Game bucket ROI (game-level ML/RL/Total Jerry brain).
--
-- Companion to prop_bucket_roi. Same pattern but for game-side markets:
-- ROI per (sport, tier, market, direction_side). Jerry synth injects
-- this per-game so his ML/RL/Total BACK/FADE/PASS decisions reference
-- historical edge, not just tier labels.

CREATE TABLE IF NOT EXISTS game_bucket_roi (
    id              bigserial   PRIMARY KEY,
    sport           text        NOT NULL,      -- MLB / NBA / NFL / etc.
    tier            text        NOT NULL,      -- PRIME / STRONG / LEAN / LIGHT
    market          text        NOT NULL,      -- ML / RL / TOTAL
    direction       text        NOT NULL,      -- HOME / AWAY / OVER / UNDER / FAV / DOG

    bucket_window   text        NOT NULL DEFAULT 'lifetime',
    wins            integer     NOT NULL DEFAULT 0,
    losses          integer     NOT NULL DEFAULT 0,
    pushes          integer     NOT NULL DEFAULT 0,
    sample_n        integer     NOT NULL DEFAULT 0,
    hit_rate        numeric,
    avg_decimal_odds numeric,
    roi_pct         numeric,
    jerry_hint      text,                       -- BACK / FADE / PASS
    hint_confidence integer,

    computed_at     timestamptz NOT NULL DEFAULT now(),

    UNIQUE (sport, tier, market, direction, bucket_window)
);

CREATE INDEX IF NOT EXISTS idx_game_bucket_roi_lookup
    ON game_bucket_roi (sport, tier, market);

ALTER TABLE game_bucket_roi DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
