-- 2026-07-16: Honest metrics split.
-- ============================================================
-- The 7/12 track_record views included batter hits_over 0.5 in the topline
-- tier record. Discovery 7/12 (project_batter_hits_signal_712 memory):
-- those are SIGNAL props at internal projection lines, not straight bets
-- — sportsbook 1+ hits alt-line juice (-300 to -450 typical) eats the edge
-- despite our 68% real-hit rate.
--
-- Publishing 61.7% as a straight-bet marketing metric would set users up to
-- lose money buying those batters at posted alt lines. This migration splits
-- the views into two honest tracks:
--
--   BETTABLE: pitcher props with book_line NOT NULL. What a user CAN act on.
--   SIGNAL:   batter hits_over 0.5 internal-line props. Model accuracy, not bet.
--
-- Both surface separately in the app with distinct framing.

-- ─────────────────────────────────────────────────────────────
-- 1. BETTABLE — pitcher props only (book_line NOT NULL)
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_prop_track_record AS
WITH graded AS (
    SELECT
        game_date, tier, prop_type,
        CASE WHEN result LIKE 'Win%' THEN 1 ELSE 0 END AS is_win,
        CASE WHEN result LIKE 'Loss%' THEN 1 ELSE 0 END AS is_loss,
        CURRENT_DATE - game_date AS days_old
    FROM mlb_pipeline_props
    WHERE result IS NOT NULL
      AND (result LIKE 'Win%' OR result LIKE 'Loss%')
      AND tier IN ('PRIME', 'STRONG')
      AND book_line IS NOT NULL                        -- HONEST FILTER: bettable only
      AND game_date >= CURRENT_DATE - INTERVAL '90 days'
)
SELECT
    tier,
    SUM(is_win) FILTER (WHERE days_old <= 7) AS wins_7d,
    SUM(is_loss) FILTER (WHERE days_old <= 7) AS losses_7d,
    ROUND(100.0 * SUM(is_win) FILTER (WHERE days_old <= 7) /
        NULLIF(SUM(is_win + is_loss) FILTER (WHERE days_old <= 7), 0), 1) AS pct_7d,
    SUM(is_win) FILTER (WHERE days_old <= 30) AS wins_30d,
    SUM(is_loss) FILTER (WHERE days_old <= 30) AS losses_30d,
    ROUND(100.0 * SUM(is_win) FILTER (WHERE days_old <= 30) /
        NULLIF(SUM(is_win + is_loss) FILTER (WHERE days_old <= 30), 0), 1) AS pct_30d,
    SUM(is_win) AS wins_90d,
    SUM(is_loss) AS losses_90d,
    ROUND(100.0 * SUM(is_win) / NULLIF(SUM(is_win + is_loss), 0), 1) AS pct_90d
FROM graded
GROUP BY tier;

CREATE OR REPLACE VIEW public.v_prop_track_record_by_type AS
WITH graded AS (
    SELECT
        tier, prop_type,
        CASE WHEN result LIKE 'Win%' THEN 1 ELSE 0 END AS is_win,
        CASE WHEN result LIKE 'Loss%' THEN 1 ELSE 0 END AS is_loss
    FROM mlb_pipeline_props
    WHERE result IS NOT NULL
      AND (result LIKE 'Win%' OR result LIKE 'Loss%')
      AND tier IN ('PRIME', 'STRONG')
      AND book_line IS NOT NULL                        -- HONEST FILTER: bettable only
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

COMMENT ON VIEW public.v_prop_track_record IS
    'Bettable pitcher props only (book_line NOT NULL). Rolling 7/30/90d.';

COMMENT ON VIEW public.v_prop_track_record_by_type IS
    'Bettable pitcher props only, 30d hit rate by (tier, prop_type). Excludes batter internal-line signals.';

-- ─────────────────────────────────────────────────────────────
-- 2. SIGNAL — batter hits_over 0.5 internal-line (book_line NULL)
--    Same rolling structure as bettable views for parity.
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_signal_track_record AS
WITH graded AS (
    SELECT
        game_date, tier, prop_type,
        CASE WHEN result LIKE 'Win%' THEN 1 ELSE 0 END AS is_win,
        CASE WHEN result LIKE 'Loss%' THEN 1 ELSE 0 END AS is_loss,
        CURRENT_DATE - game_date AS days_old
    FROM mlb_pipeline_props
    WHERE result IS NOT NULL
      AND (result LIKE 'Win%' OR result LIKE 'Loss%')
      AND tier IN ('PRIME', 'STRONG')
      AND book_line IS NULL                            -- SIGNAL: internal-line only
      AND prop_type IN ('hits_over', 'hits_under')     -- batter surface
      AND game_date >= CURRENT_DATE - INTERVAL '90 days'
)
SELECT
    tier,
    SUM(is_win) FILTER (WHERE days_old <= 7) AS wins_7d,
    SUM(is_loss) FILTER (WHERE days_old <= 7) AS losses_7d,
    ROUND(100.0 * SUM(is_win) FILTER (WHERE days_old <= 7) /
        NULLIF(SUM(is_win + is_loss) FILTER (WHERE days_old <= 7), 0), 1) AS pct_7d,
    SUM(is_win) FILTER (WHERE days_old <= 30) AS wins_30d,
    SUM(is_loss) FILTER (WHERE days_old <= 30) AS losses_30d,
    ROUND(100.0 * SUM(is_win) FILTER (WHERE days_old <= 30) /
        NULLIF(SUM(is_win + is_loss) FILTER (WHERE days_old <= 30), 0), 1) AS pct_30d,
    SUM(is_win) AS wins_90d,
    SUM(is_loss) AS losses_90d,
    ROUND(100.0 * SUM(is_win) / NULLIF(SUM(is_win + is_loss), 0), 1) AS pct_90d
FROM graded
GROUP BY tier;

COMMENT ON VIEW public.v_signal_track_record IS
    'Batter hits internal-line signal accuracy (NOT bettable — alt-line juice varies). Model correctness only.';

GRANT SELECT ON public.v_prop_track_record TO anon, authenticated;
GRANT SELECT ON public.v_prop_track_record_by_type TO anon, authenticated;
GRANT SELECT ON public.v_signal_track_record TO anon, authenticated;

NOTIFY pgrst, 'reload schema';

-- Quick sanity dump
SELECT 'v_prop_track_record' AS view, tier, pct_30d, wins_30d, losses_30d
FROM public.v_prop_track_record
UNION ALL
SELECT 'v_signal_track_record' AS view, tier, pct_30d, wins_30d, losses_30d
FROM public.v_signal_track_record
ORDER BY view, tier;
