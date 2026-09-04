-- 2026-09-04 v_dawg_track_record_by_tier
-- ================================================================
-- Byte-sized rollup view for Track Record UI byTier breakdown.
-- Replaces client-side aggregation of 1500 daily_dawg rows.
--
-- Shape: (tier, wins, losses, n, pct)
-- Reads: daily_dawg (result NOT NULL, last 90d)
-- ================================================================

CREATE OR REPLACE VIEW public.v_dawg_track_record_by_tier AS
WITH graded AS (
    SELECT tier,
           CASE WHEN result = 'Win'  THEN 1 ELSE 0 END AS is_win,
           CASE WHEN result = 'Loss' THEN 1 ELSE 0 END AS is_loss
    FROM daily_dawg
    WHERE result IN ('Win', 'Loss')
      AND tier IN ('PRIME', 'STRONG', 'LEAN')
      AND game_date >= CURRENT_DATE - INTERVAL '90 days'
)
SELECT
    tier,
    SUM(is_win)::int  AS wins,
    SUM(is_loss)::int AS losses,
    SUM(is_win + is_loss)::int AS n,
    ROUND(100.0 * SUM(is_win) / NULLIF(SUM(is_win + is_loss), 0), 1) AS pct
FROM graded
GROUP BY tier
ORDER BY CASE tier WHEN 'PRIME' THEN 1 WHEN 'STRONG' THEN 2 WHEN 'LEAN' THEN 3 END;

COMMENT ON VIEW public.v_dawg_track_record_by_tier IS
    'Rollup for Track Record UI byTier chip on Dawg surface. '
    '90-day window, resolved picks only.';

NOTIFY pgrst, 'reload schema';
