-- 2026-09-13 SEASON WEEK ANCHOR FIX — Week 1 Thu = 2026-09-11, not 2026-09-04
-- ================================================================
-- Andy 9/13: "For the 1000th time this is still week 1" — my prior
-- migration 20260913f used 2026-09-04 as the NFL Week 1 Thursday
-- anchor based on a real-world-NFL mental model. WRONG for this
-- environment. Sweat Shop 2026 season Week 1 opener is Thu 2026-09-11
-- (TNF). Games on 9/13 Sun + 9/14 Mon MNF = Week 1. Week 2 starts
-- Thu 2026-09-18.
--
-- Verified against DB: `SELECT DISTINCT week, game_date FROM
-- nfl_game_context WHERE season=2026` shows week=1 for game_date
-- range 2026-09-10 to 2026-09-15. Anchor must match.
--
-- Rebackfill season_week using the correct anchor. Also update the
-- current_nfl_season_week() SQL helper.
-- ================================================================

-- ------------------------------------------------------------
-- NFL: rebackfill season_week from correct 9/11 anchor
-- ------------------------------------------------------------

-- Clear the wrong values first (they were computed from 9/4 anchor)
UPDATE public.nfl_game_context
   SET season_week = NULL
 WHERE season = 2026;

-- Backfill from correct anchor: Week 1 Thu = 2026-09-11
-- Week N spans days [wk1_thu + (N-1)*7, wk1_thu + N*7)
UPDATE public.nfl_game_context
   SET season_week = GREATEST(
       0,
       FLOOR((game_date - DATE '2026-09-11') / 7)::INTEGER + 1
   )
 WHERE season = 2026
   AND season_week IS NULL;

-- Preseason clamp (before 9/11 = week 0)
UPDATE public.nfl_game_context
   SET season_week = 0
 WHERE season = 2026
   AND game_date < DATE '2026-09-11';

-- ------------------------------------------------------------
-- Fix the current_nfl_season_week() helper function
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.current_nfl_season_week()
RETURNS INTEGER
LANGUAGE SQL
STABLE
AS $$
    WITH today_et AS (
        SELECT (NOW() AT TIME ZONE 'America/New_York')::DATE AS d
    ),
    anchor AS (
        SELECT DATE '2026-09-11' AS wk1_thu
    ),
    -- Roll forward: if today is Tue or Wed (post-Mon MNF), advance to
    -- next week's Thursday
    computed AS (
        SELECT
            CASE
                WHEN EXTRACT(DOW FROM (SELECT d FROM today_et)) IN (2, 3)
                    THEN GREATEST(0, FLOOR(((SELECT d FROM today_et) - (SELECT wk1_thu FROM anchor)) / 7)::INTEGER + 2)
                ELSE GREATEST(0, FLOOR(((SELECT d FROM today_et) - (SELECT wk1_thu FROM anchor)) / 7)::INTEGER + 1)
            END AS wk
    )
    SELECT wk FROM computed;
$$;

COMMENT ON FUNCTION public.current_nfl_season_week() IS
    'Returns the current NFL season week. Anchor: 2026-09-11 = Week 1 Thu. '
    'Rolls forward on Tue/Wed so post-MNF views see the upcoming Thu slate. '
    'Corrected 2026-09-13 (superseded 9/4 anchor from migration 20260913f).';

NOTIFY pgrst, 'reload schema';
