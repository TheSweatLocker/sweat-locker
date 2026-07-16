-- 2026-07-12: Track record aggregation views for public /track-record page.
-- Discovery 7/12: POTD is 43% 30d (bad marketing surface); props are 57-61%.
-- Views expose rolling 7d / 30d / 90d aggregates the app can read without
-- needing to run n=1000+ aggregations client-side.

-- ─────────────────────────────────────────────────────────────────────
-- 1. Prop record by tier + window
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_prop_track_record AS
WITH graded AS (
    SELECT
        game_date,
        tier,
        prop_type,
        CASE WHEN result LIKE 'Win%' THEN 1 ELSE 0 END AS is_win,
        CASE WHEN result LIKE 'Loss%' THEN 1 ELSE 0 END AS is_loss,
        CURRENT_DATE - game_date AS days_old
    FROM mlb_pipeline_props
    WHERE result IS NOT NULL
      AND (result LIKE 'Win%' OR result LIKE 'Loss%')
      AND tier IN ('PRIME', 'STRONG')
      AND game_date >= CURRENT_DATE - INTERVAL '90 days'
)
SELECT
    tier,
    -- 7d
    SUM(is_win) FILTER (WHERE days_old <= 7) AS wins_7d,
    SUM(is_loss) FILTER (WHERE days_old <= 7) AS losses_7d,
    ROUND(100.0 * SUM(is_win) FILTER (WHERE days_old <= 7) /
        NULLIF(SUM(is_win + is_loss) FILTER (WHERE days_old <= 7), 0), 1) AS pct_7d,
    -- 30d
    SUM(is_win) FILTER (WHERE days_old <= 30) AS wins_30d,
    SUM(is_loss) FILTER (WHERE days_old <= 30) AS losses_30d,
    ROUND(100.0 * SUM(is_win) FILTER (WHERE days_old <= 30) /
        NULLIF(SUM(is_win + is_loss) FILTER (WHERE days_old <= 30), 0), 1) AS pct_30d,
    -- 90d
    SUM(is_win) AS wins_90d,
    SUM(is_loss) AS losses_90d,
    ROUND(100.0 * SUM(is_win) / NULLIF(SUM(is_win + is_loss), 0), 1) AS pct_90d
FROM graded
GROUP BY tier;

COMMENT ON VIEW public.v_prop_track_record IS
    'Prop win rate rolling 7/30/90d by tier. Card-eligible (PRIME/STRONG) only. Read by public track record page.';

-- ─────────────────────────────────────────────────────────────────────
-- 2. Prop record by (tier, prop_type) — the bucket-level truth
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_prop_track_record_by_type AS
WITH graded AS (
    SELECT
        tier,
        prop_type,
        CASE WHEN result LIKE 'Win%' THEN 1 ELSE 0 END AS is_win,
        CASE WHEN result LIKE 'Loss%' THEN 1 ELSE 0 END AS is_loss
    FROM mlb_pipeline_props
    WHERE result IS NOT NULL
      AND (result LIKE 'Win%' OR result LIKE 'Loss%')
      AND tier IN ('PRIME', 'STRONG')
      AND game_date >= CURRENT_DATE - INTERVAL '30 days'
)
SELECT
    tier,
    prop_type,
    SUM(is_win) AS wins,
    SUM(is_loss) AS losses,
    SUM(is_win + is_loss) AS n,
    ROUND(100.0 * SUM(is_win) / NULLIF(SUM(is_win + is_loss), 0), 1) AS pct
FROM graded
GROUP BY tier, prop_type
HAVING SUM(is_win + is_loss) >= 5
ORDER BY tier, pct DESC;

COMMENT ON VIEW public.v_prop_track_record_by_type IS
    '30d hit rate by (tier, prop_type). Same shape as prop_edge_calibration but for public display.';

-- ─────────────────────────────────────────────────────────────────────
-- 3. Daily best-bet history summary (POTD track record)
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_potd_track_record AS
WITH graded AS (
    SELECT
        bet_date,
        sport,
        CASE WHEN result = 'Win' THEN 1 ELSE 0 END AS is_win,
        CASE WHEN result = 'Loss' THEN 1 ELSE 0 END AS is_loss,
        CASE WHEN result = 'Push' THEN 1 ELSE 0 END AS is_push,
        CURRENT_DATE - bet_date AS days_old
    FROM daily_best_bet_history
    WHERE result IN ('Win', 'Loss', 'Push')
      AND bet_date >= CURRENT_DATE - INTERVAL '90 days'
)
SELECT
    sport,
    SUM(is_win) FILTER (WHERE days_old <= 7) AS wins_7d,
    SUM(is_loss) FILTER (WHERE days_old <= 7) AS losses_7d,
    SUM(is_push) FILTER (WHERE days_old <= 7) AS push_7d,
    SUM(is_win) FILTER (WHERE days_old <= 30) AS wins_30d,
    SUM(is_loss) FILTER (WHERE days_old <= 30) AS losses_30d,
    SUM(is_push) FILTER (WHERE days_old <= 30) AS push_30d,
    SUM(is_win) AS wins_90d,
    SUM(is_loss) AS losses_90d,
    SUM(is_push) AS push_90d
FROM graded
GROUP BY sport;

COMMENT ON VIEW public.v_potd_track_record IS
    'Play-of-the-Day rolling 7/30/90d record per sport.';

-- ─────────────────────────────────────────────────────────────────────
-- 4. Grants + reload
-- ─────────────────────────────────────────────────────────────────────
GRANT SELECT ON public.v_prop_track_record TO anon, authenticated;
GRANT SELECT ON public.v_prop_track_record_by_type TO anon, authenticated;
GRANT SELECT ON public.v_potd_track_record TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
