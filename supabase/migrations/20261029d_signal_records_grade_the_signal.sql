ALTER TABLE public.signal_attribution
    ADD COLUMN IF NOT EXISTS signal_was_right BOOLEAN;

CREATE OR REPLACE VIEW public.v_signal_records AS
WITH graded AS (
    SELECT
        sport,
        signal_key,
        kind,
        signal_was_right,
        game_date
    FROM public.signal_attribution
    WHERE result IN ('W', 'L', 'P')
      AND signal_was_right IS NOT NULL
),
d30 AS (
    SELECT
        sport, signal_key, kind,
        COUNT(*) FILTER (WHERE signal_was_right)::INT AS w30,
        COUNT(*) FILTER (WHERE NOT signal_was_right)::INT AS l30,
        0::INT AS p30
    FROM graded
    WHERE game_date >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY sport, signal_key, kind
),
d90 AS (
    SELECT
        sport, signal_key, kind,
        COUNT(*) FILTER (WHERE signal_was_right)::INT AS w90,
        COUNT(*) FILTER (WHERE NOT signal_was_right)::INT AS l90
    FROM graded
    WHERE game_date >= CURRENT_DATE - INTERVAL '90 days'
    GROUP BY sport, signal_key, kind
),
lifetime AS (
    SELECT
        sport, signal_key, kind,
        COUNT(*) FILTER (WHERE signal_was_right)::INT AS w_all,
        COUNT(*) FILTER (WHERE NOT signal_was_right)::INT AS l_all
    FROM graded
    GROUP BY sport, signal_key, kind
)
SELECT
    l.sport, l.signal_key, l.kind,
    COALESCE(d30.w30, 0) AS wins_30d,
    COALESCE(d30.l30, 0) AS losses_30d,
    COALESCE(d30.p30, 0) AS pushes_30d,
    CASE WHEN (COALESCE(d30.w30,0) + COALESCE(d30.l30,0)) > 0
         THEN ROUND(100.0 * COALESCE(d30.w30, 0) / (COALESCE(d30.w30,0) + COALESCE(d30.l30,0)), 1)
         ELSE NULL END AS hit_pct_30d,
    COALESCE(d90.w90, 0) AS wins_90d,
    COALESCE(d90.l90, 0) AS losses_90d,
    CASE WHEN (COALESCE(d90.w90,0) + COALESCE(d90.l90,0)) > 0
         THEN ROUND(100.0 * COALESCE(d90.w90, 0) / (COALESCE(d90.w90,0) + COALESCE(d90.l90,0)), 1)
         ELSE NULL END AS hit_pct_90d,
    COALESCE(l.w_all, 0) AS wins_lifetime,
    COALESCE(l.l_all, 0) AS losses_lifetime,
    CASE WHEN (COALESCE(l.w_all,0) + COALESCE(l.l_all,0)) > 0
         THEN ROUND(100.0 * COALESCE(l.w_all, 0) / (COALESCE(l.w_all,0) + COALESCE(l.l_all,0)), 1)
         ELSE NULL END AS hit_pct_lifetime
FROM lifetime l
LEFT JOIN d30 ON l.sport = d30.sport AND l.signal_key = d30.signal_key AND l.kind = d30.kind
LEFT JOIN d90 ON l.sport = d90.sport AND l.signal_key = d90.signal_key AND l.kind = d90.kind
WHERE (COALESCE(l.w_all,0) + COALESCE(l.l_all,0)) >= 5;

COMMENT ON VIEW public.v_signal_records IS
    'Per-(sport, signal_key, kind) hit rate for THE SIGNAL, over 30d / 90d / '
    'lifetime. Counts signal_attribution.signal_was_right, which grades '
    'signal_side against the stored margin and close line with the per-sport '
    'sign convention. It does NOT count result: result is the PICKs outcome '
    'stamped onto every signal row that fired on it, so counting it showed a '
    'signal our own selection record - inflated up to 10.8pp (NCAAF '
    'COHORT_NCAAF_HEAVY_HOME_FAV 61.7 shown vs 50.9 real, NFL LR_SHADOW 53.3 '
    'vs 42.7) and biased against any signal that DISAGREED with the engine. '
    'Rows where the signal is not countable (NEUTRAL subject, cross-axis, '
    'push) are excluded rather than defaulted. Sample gate: 5+ graded.';

GRANT SELECT ON public.v_signal_records TO anon, authenticated;
