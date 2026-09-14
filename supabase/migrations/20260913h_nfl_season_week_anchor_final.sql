-- 2026-09-13 SEASON WEEK ANCHOR — CORRECT (finally): Week 1 = 2026-09-09 through 2026-09-15
-- ================================================================
-- Andy 9/13 THIRD correction: "Week 1 started on the 9th and ends
-- after monday game tomorrow." Prior attempts used 2026-09-04 (real-
-- world NFL Sep 4 opener — wrong) and 2026-09-11 (Thu of "Week 1"
-- based on TNF anchor — also wrong).
--
-- Correct anchor: **2026-09-09** (Tue, the actual first day of Sweat
-- Shop 2026 Week 1). Week 1 spans 9/9-9/15 inclusive. Week 2 starts
-- 9/16. This matches Andy's authority + the DB storage (week=1 for
-- 9/10-9/15 games with room for a possible 9/9 game).
--
-- Rebackfill season_week using 9/9 anchor. Also update the
-- current_nfl_season_week() helper.
-- ================================================================

-- Rebackfill: clear + recompute from 9/9 anchor
UPDATE public.nfl_game_context
   SET season_week = NULL
 WHERE season = 2026;

UPDATE public.nfl_game_context
   SET season_week = GREATEST(
       0,
       FLOOR((game_date - DATE '2026-09-09') / 7)::INTEGER + 1
   )
 WHERE season = 2026
   AND season_week IS NULL;

UPDATE public.nfl_game_context
   SET season_week = 0
 WHERE season = 2026
   AND game_date < DATE '2026-09-09';

-- Corrected helper function
CREATE OR REPLACE FUNCTION public.current_nfl_season_week()
RETURNS INTEGER
LANGUAGE SQL
STABLE
AS $$
    WITH today_et AS (
        SELECT (NOW() AT TIME ZONE 'America/New_York')::DATE AS d
    ),
    anchor AS (
        SELECT DATE '2026-09-09' AS wk1_start
    ),
    -- Roll forward: Tue (dow=2) is post-Mon-MNF, advance to next week
    computed AS (
        SELECT
            CASE
                WHEN EXTRACT(DOW FROM (SELECT d FROM today_et)) = 2
                    THEN GREATEST(0, FLOOR(((SELECT d FROM today_et) - (SELECT wk1_start FROM anchor)) / 7)::INTEGER + 2)
                ELSE GREATEST(0, FLOOR(((SELECT d FROM today_et) - (SELECT wk1_start FROM anchor)) / 7)::INTEGER + 1)
            END AS wk
    )
    SELECT wk FROM computed;
$$;

COMMENT ON FUNCTION public.current_nfl_season_week() IS
    'Returns current NFL season week. Anchor: 2026-09-09 = Week 1 start. '
    'Week 1 spans 9/9-9/15 inclusive; Week 2 starts 9/16. Rolls forward '
    'on Tuesday (post-Mon MNF). Corrected 2026-09-13 (third attempt — '
    'see feedback_nfl_2026_week1_anchor for anchor history).';

NOTIFY pgrst, 'reload schema';
