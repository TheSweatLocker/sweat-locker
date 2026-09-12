-- 2026-09-11 NFL QB HOME/AWAY SPLIT SIGNAL SOURCES
-- ================================================================
-- Wires 4 new game-level signals into the ensemble scorer's signal
-- source registry. These read the ctx fields attached by
-- _enrich_nfl_qb_home_away_splits() in nfl_game_context.py.
--
-- Requires:
--   · Migration 20260911b_nfl_qb_home_away_splits.sql applied
--   · compute_nfl_qb_home_away_splits.py has been run at least once
--
-- All four are class='qb_split' (new signal class — helps intra-class
-- family dedup so we don't double-count multiple QB-split signals on
-- the same side).
--
-- Sample size gates (starts >= 15 career or >= 5 recent) prevent
-- fires on rookies or partial-season samples. Deltas expressed in
-- percentage points × 100 (e.g. 15pp = +15 percentage points).
-- ================================================================

INSERT INTO public.signal_sources (
    signal_key, sport, class, market_scope, subject_scope,
    condition_expr, side_expr, strength_expr,
    display_prose_template, enabled, origin,
    weight_registry_key, hit_rate_pct, sample_n
) VALUES

-- 1. HOME QB dominant at home → HOME_ML signal
--    Fires when: home QB has ≥15pp career home-over-road delta AND
--    ≥15 home starts (excludes rookies + small-sample bias).
(
    'nfl_home_qb_home_dominant',
    'NFL', 'qb_split', 'ml', 'game',
    'ctx.home_qb_career_h_r_delta_pp is not None '
    'and ctx.home_qb_career_home_starts is not None '
    'and ctx.home_qb_career_home_starts >= 15 '
    'and float(ctx.home_qb_career_h_r_delta_pp) >= 15.0',
    '"HOME_ML"',
    'min(float(ctx.home_qb_career_h_r_delta_pp) / 30.0, 1.0)',
    'Home QB {home_qb_name} +{home_qb_career_h_r_delta_pp}pp career at home vs road ({home_qb_career_home_starts} home starts)',
    true, 'SEEDED_QB_SPLITS_911', NULL, NULL, NULL
),

-- 2. AWAY QB struggles on road → HOME_ML signal (fade the road QB)
--    Fires when: away QB has ≥15pp career home-over-road delta AND
--    ≥15 road starts. Same threshold from the road-QB side.
(
    'nfl_away_qb_road_struggles',
    'NFL', 'qb_split', 'ml', 'game',
    'ctx.away_qb_career_h_r_delta_pp is not None '
    'and ctx.away_qb_career_road_starts is not None '
    'and ctx.away_qb_career_road_starts >= 15 '
    'and float(ctx.away_qb_career_h_r_delta_pp) >= 15.0',
    '"HOME_ML"',
    'min(float(ctx.away_qb_career_h_r_delta_pp) / 30.0, 1.0)',
    'Road QB {away_qb_name} +{away_qb_career_h_r_delta_pp}pp home-vs-road career gap ({away_qb_career_road_starts} road starts) — road-side struggle',
    true, 'SEEDED_QB_SPLITS_911', NULL, NULL, NULL
),

-- 3. AWAY QB INVERSE — actually better on the road (rare pattern)
--    Fires when: away QB has ≤ -10pp delta (BETTER on road than home)
--    AND ≥10 road starts. Historical exemplar: Davis Mills, Drake Maye.
--    Take the AWAY_ML — this is the anti-market signal.
(
    'nfl_away_qb_road_inverse',
    'NFL', 'qb_split', 'ml', 'game',
    'ctx.away_qb_career_h_r_delta_pp is not None '
    'and ctx.away_qb_career_road_starts is not None '
    'and ctx.away_qb_career_road_starts >= 10 '
    'and float(ctx.away_qb_career_h_r_delta_pp) <= -10.0',
    '"AWAY_ML"',
    'min(abs(float(ctx.away_qb_career_h_r_delta_pp)) / 25.0, 1.0)',
    'Road QB {away_qb_name} inverse split — {away_qb_career_road_win_pct} on road vs {away_qb_career_home_win_pct} at home ({away_qb_career_road_starts} road starts)',
    true, 'SEEDED_QB_SPLITS_911', NULL, NULL, NULL
),

-- 4. HOME QB recent home form hot → HOME_ML signal
--    Fires when: home QB has ≥65% win rate in last 10 home starts.
--    Recent form matters more than career for short-window betting.
(
    'nfl_home_qb_recent_home_hot',
    'NFL', 'qb_split', 'ml', 'game',
    'ctx.home_qb_recent_home_win_pct is not None '
    'and ctx.home_qb_recent_home_starts is not None '
    'and ctx.home_qb_recent_home_starts >= 5 '
    'and float(ctx.home_qb_recent_home_win_pct) >= 0.65',
    '"HOME_ML"',
    'min((float(ctx.home_qb_recent_home_win_pct) - 0.5) * 2.0, 1.0)',
    'Home QB {home_qb_name} {home_qb_recent_home_wins}-{home_qb_recent_home_starts} last home starts',
    true, 'SEEDED_QB_SPLITS_911', NULL, NULL, NULL
)

ON CONFLICT (signal_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
