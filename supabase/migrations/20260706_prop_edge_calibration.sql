-- Prop edge calibration table + backtest history
-- ============================================================
-- Discovered 2026-07-05 during 5-day prop audit: real edge lives at the
-- (tier, prop_type, direction) bucket level, not tier-wide. STRONG tier
-- currently 43% on 5-day, but STRONG bb_under hits 74% (n=35). Full
-- 33-day backtest of allow-list rule delivers +10.7pp lift.
--
-- prop_edge_calibrator.py runs nightly, recomputes buckets from a rolling
-- 30-day window, writes rows here. Prop scorer reads this table to
-- downgrade NEUTRAL props and skip KILL buckets before writing tiers.

CREATE TABLE IF NOT EXISTS prop_edge_calibration (
    id BIGSERIAL PRIMARY KEY,
    tier TEXT NOT NULL,                    -- 'PRIME' | 'STRONG'
    prop_type TEXT NOT NULL,               -- 'bb_under', 'ha_over', etc
    direction TEXT NOT NULL,               -- 'over' | 'under'
    hit_rate NUMERIC(4,1) NOT NULL,        -- 0.0 to 100.0
    sample_size INT NOT NULL,              -- n graded picks in window
    category TEXT NOT NULL CHECK (category IN ('KEEP','KILL','NEUTRAL')),
    computed_at DATE NOT NULL,
    window_days INT NOT NULL DEFAULT 30,
    UNIQUE (tier, prop_type, direction, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_prop_edge_cal_computed
    ON prop_edge_calibration (computed_at DESC);

-- Weekly backtest results — validates the current allow-list against a
-- rolling holdout window. Lets us track calibration drift over time.
CREATE TABLE IF NOT EXISTS prop_edge_backtest_history (
    id BIGSERIAL PRIMARY KEY,
    ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    training_window_days INT NOT NULL,     -- e.g. 30
    test_window_days INT NOT NULL,         -- e.g. 7
    test_start DATE NOT NULL,
    test_end DATE NOT NULL,
    raw_hits INT NOT NULL,
    raw_losses INT NOT NULL,
    raw_hit_rate NUMERIC(4,1) NOT NULL,
    filtered_hits INT NOT NULL,
    filtered_losses INT NOT NULL,
    filtered_hit_rate NUMERIC(4,1) NOT NULL,
    keep_buckets_count INT NOT NULL,
    delta_pp NUMERIC(4,1) NOT NULL          -- filtered - raw, percentage points
);
CREATE INDEX IF NOT EXISTS idx_prop_edge_bt_ran
    ON prop_edge_backtest_history (ran_at DESC);

GRANT SELECT ON prop_edge_calibration TO anon, authenticated;
GRANT SELECT ON prop_edge_backtest_history TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
