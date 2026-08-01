-- 2026-08-01d · Prop bucket ROI table (the "Jerry brain" for prop decisions).
--
-- Backtested juice-adjusted return-on-investment per
-- (sport, tier, prop_type, direction) bucket. Refreshed nightly from graded
-- props. Jerry synth injects this per-prop into the prompt so BACK/FADE/PASS
-- decisions reference REAL historical edge, not just tier labels.
--
-- Key finding driving this build (8/1 mining):
--   - SKIP outs_under: +38.8% ROI (n=79) — SKIP tier hides gold
--   - PRIME hits_over: 67.6% hit but ROI unknown (missing odds)
--   - STRONG er_over: -22.8% ROI (n=40) — bleed money we've been backing
--
-- Solution: tier labels stay STABLE for user consistency; Jerry overlays
-- BACK/FADE/PASS informed by this ROI table. App renders both so users see
-- "SKIP tier · Jerry BACK 78 · historically +38.8% ROI on this bucket."

CREATE TABLE IF NOT EXISTS prop_bucket_roi (
    id              bigserial   PRIMARY KEY,
    sport           text        NOT NULL,      -- MLB / NBA / NFL / etc.
    tier            text        NOT NULL,      -- PRIME / STRONG / LEAN / SKIP / COVERAGE
    prop_type       text        NOT NULL,      -- ks / bb / er / ha / outs / hits (family)
    direction       text        NOT NULL,      -- over / under

    -- Rolling window stats (window is fixed per table row, snapshot at computed_at)
    bucket_window   text        NOT NULL DEFAULT 'lifetime',  -- 'lifetime' | '90d' | '30d'
    wins            integer     NOT NULL DEFAULT 0,
    losses          integer     NOT NULL DEFAULT 0,
    pushes          integer     NOT NULL DEFAULT 0,
    sample_n        integer     NOT NULL DEFAULT 0,
    hit_rate        numeric,                    -- wins / (wins + losses)
    avg_decimal_odds numeric,                   -- avg dec odds across sample (1.83 avg = -120 avg)
    roi_pct         numeric,                    -- juice-adjusted ROI (%). Nullable when odds missing.
    -- Verdict hint for Jerry: BACK / FADE / PASS based on roi_pct + hit_rate + sample_n
    jerry_hint      text,
    hint_confidence integer,                    -- 0-100 based on roi magnitude × sample size

    computed_at     timestamptz NOT NULL DEFAULT now(),

    UNIQUE (sport, tier, prop_type, direction, bucket_window)
);

CREATE INDEX IF NOT EXISTS idx_prop_bucket_roi_lookup
    ON prop_bucket_roi (sport, tier, prop_type, direction);

ALTER TABLE prop_bucket_roi DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
