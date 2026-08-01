-- 2026-08-01b · Totals cohort signals attribution table.
--
-- Sport-universal cohort framework for TOTALS (OVER/UNDER) — mirrors
-- the sides-only cohort framework that already exists for ML/RL
-- markets. Fills the gap surfaced by the 7/31 launch-day audit:
-- Jerry's sides synthesis had cohort backing but totals had NONE,
-- forcing Jerry's totals reads to lean on raw signals with no
-- historical attribution to gate PRIME vs LEAN.
--
-- Rows are one per (sport, cohort_name, direction) with rolling
-- hit-rate windows. Nightly backfill recomputes from graded results.
--
-- MLB signals seeded first; NCAAB (Nov 2026 season) and NFL
-- (Sept 2026 season) added when their result-tracking pipelines
-- have enough history.

CREATE TABLE IF NOT EXISTS totals_cohort_signals (
    id              bigserial   PRIMARY KEY,

    sport           text        NOT NULL,        -- 'MLB' | 'NCAAB' | 'NFL' | 'NBA'
    cohort_name     text        NOT NULL,        -- e.g. 'sp_short_rest', 'coming_off_road_gte_6'
    direction       text        NOT NULL,        -- 'OVER' | 'UNDER'

    -- Rolling windows
    lifetime_pct    numeric,
    lifetime_n      integer     DEFAULT 0,
    last_30d_pct    numeric,
    last_30d_n      integer     DEFAULT 0,
    last_14d_pct    numeric,
    last_14d_n      integer     DEFAULT 0,

    -- Optional cohort context (human-readable description for Jerry prompt)
    description     text,
    -- Optional trigger threshold JSON (e.g. {"rest_days_lt": 5})
    trigger_config  jsonb,

    computed_at     timestamptz NOT NULL DEFAULT now(),

    UNIQUE (sport, cohort_name, direction)
);

CREATE INDEX IF NOT EXISTS idx_totals_cohort_signals_lookup
    ON totals_cohort_signals (sport, direction);

ALTER TABLE totals_cohort_signals DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
