-- 2026-09-11 QB HOME/AWAY SPLITS AGGREGATION TABLE
-- ================================================================
-- Materialized-by-cron table holding per-QB career + recent home/road
-- splits, computed from nfl_game_results (which has home_qb_id +
-- away_qb_id + home_win going back to 2020, ~1000 rows).
--
-- Consumed by:
--   · nfl_game_context.py — JOIN on home_qb_id / away_qb_id to attach
--     splits to ctx, feeding new signal_sources rows
--   · Prop/game signal handlers — home_qb_home_dominant_over,
--     away_qb_road_struggles_under, road_qb_short_week_fade
--
-- Refresh cadence: compute_nfl_qb_home_away_splits.py runs weekly
-- (Tuesday morning after MNF) so it always has the previous week's
-- games rolled in before the next slate composes.
--
-- Why a separate table (not a view):
--   The compute involves a WINDOW-style aggregation (careerhome vs
--   careerroad per QB) across ~1000 rows plus recent-N slicing.
--   Cheaper as a materialized daily job than an on-request view for
--   every ctx build (18 nfl_game_context ctx builds/day × N QBs).
-- ================================================================

CREATE TABLE IF NOT EXISTS public.nfl_qb_home_away_splits (
    qb_id                TEXT PRIMARY KEY,
    qb_name              TEXT NOT NULL,

    -- Career (all seasons in nfl_game_results)
    home_starts          INTEGER NOT NULL DEFAULT 0,
    home_wins            INTEGER NOT NULL DEFAULT 0,
    road_starts          INTEGER NOT NULL DEFAULT 0,
    road_wins            INTEGER NOT NULL DEFAULT 0,
    home_win_pct         NUMERIC(5,3),         -- home_wins / home_starts
    road_win_pct         NUMERIC(5,3),         -- road_wins / road_starts
    career_h_r_delta_pp  NUMERIC(6,2),         -- (home_pct - road_pct) × 100

    -- Recent (last 10 home + last 10 road starts, computed by date desc)
    recent_home_starts   INTEGER NOT NULL DEFAULT 0,
    recent_home_wins     INTEGER NOT NULL DEFAULT 0,
    recent_road_starts   INTEGER NOT NULL DEFAULT 0,
    recent_road_wins     INTEGER NOT NULL DEFAULT 0,
    recent_home_win_pct  NUMERIC(5,3),
    recent_road_win_pct  NUMERIC(5,3),
    recent_h_r_delta_pp  NUMERIC(6,2),

    -- Provenance
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_row_count     INTEGER                -- how many results rows fed this
);

CREATE INDEX IF NOT EXISTS nfl_qb_home_away_splits_name_idx
    ON public.nfl_qb_home_away_splits (LOWER(qb_name));

-- RLS: read-only for anon (app never writes)
ALTER TABLE public.nfl_qb_home_away_splits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read ON public.nfl_qb_home_away_splits;
CREATE POLICY public_read ON public.nfl_qb_home_away_splits
    FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS public_write ON public.nfl_qb_home_away_splits;
CREATE POLICY public_write ON public.nfl_qb_home_away_splits
    FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

COMMENT ON TABLE public.nfl_qb_home_away_splits IS
    'Per-QB career + last-10 home/road W-L splits. Materialized weekly '
    'by compute_nfl_qb_home_away_splits.py from nfl_game_results. Joined '
    'into nfl_game_context on home_qb_id/away_qb_id at scoring time.';

NOTIFY pgrst, 'reload schema';
