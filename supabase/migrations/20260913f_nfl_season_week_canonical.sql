-- 2026-09-13 CANONICAL SEASON WEEK COLUMN — nfl_game_context + ncaaf_game_context
-- ================================================================
-- Andy 9/13: "what all is controlled backside in determining cutoff
-- for week and list of games in this week verse next week." Answer
-- pre-this-migration: date arithmetic on the CLIENT (Thu/Wed anchor +
-- 7-day windows) with no backend source of truth. The `week` column
-- on nfl_game_context is contaminated with preseason values + off by
-- one from real NFL calendar (this weekend's games stored as week=1
-- while real NFL says Week 2). Fragile — bug class already bit us
-- with the 10-day-forward window bleed.
--
-- Fix: add a NEW column `season_week` that is authoritatively
-- populated from game_date per the real NFL calendar. Client filters
-- `where season=X and season_week=Y` — no more date arithmetic.
--
-- NFL 2026 Week 1 = Thu 2026-09-04. Weeks run Thu → Wed. Week N
-- includes all games where 2026-09-04 + (N-1)*7 <= game_date <
-- 2026-09-04 + N*7. Preseason games (before 2026-09-04) get
-- season_week=0.
--
-- NCAAF 2026 Week 1 = Sat 2026-08-23. Week 0 = 2026-08-22 (Week 0
-- game night). Weeks run roughly Wed → Tue. Simpler heuristic: bucket
-- by floor((game_date - 2026-08-24) / 7) + 1.
-- ================================================================

-- ------------------------------------------------------------
-- NFL
-- ------------------------------------------------------------

ALTER TABLE public.nfl_game_context
    ADD COLUMN IF NOT EXISTS season_week INTEGER;

COMMENT ON COLUMN public.nfl_game_context.season_week IS
    'Real-NFL-calendar week number. Season week 1 = kickoff Thursday '
    'onward. Preseason games = 0. Populated from game_date via '
    'backfill + writer. Client queries this column exclusively for '
    'This Week / Next Week filtering — replaces prior client-side '
    'date arithmetic.';

-- Backfill 2026 season from game_date. Week 1 Thu = 2026-09-04.
-- Week N spans days [wk1_thu + (N-1)*7, wk1_thu + N*7).
UPDATE public.nfl_game_context
   SET season_week = GREATEST(
       0,
       FLOOR((game_date - DATE '2026-09-04') / 7)::INTEGER + 1
   )
 WHERE season = 2026
   AND season_week IS NULL;

-- Sanity: preseason 8/x games with negative distance clamp to 0
UPDATE public.nfl_game_context
   SET season_week = 0
 WHERE season = 2026
   AND game_date < DATE '2026-09-04';

CREATE INDEX IF NOT EXISTS nfl_game_context_season_week
    ON public.nfl_game_context (season, season_week);

-- ------------------------------------------------------------
-- NCAAF
-- ------------------------------------------------------------

ALTER TABLE public.ncaaf_game_context
    ADD COLUMN IF NOT EXISTS season_week INTEGER;

COMMENT ON COLUMN public.ncaaf_game_context.season_week IS
    'Real NCAAF calendar week. Week 1 = 2026-08-24 (Sun) onward. '
    'Week 0 = pre-8/24 games. Populated by backfill + writer.';

UPDATE public.ncaaf_game_context
   SET season_week = GREATEST(
       0,
       FLOOR((game_date - DATE '2026-08-24') / 7)::INTEGER + 1
   )
 WHERE season = 2026
   AND season_week IS NULL;

UPDATE public.ncaaf_game_context
   SET season_week = 0
 WHERE season = 2026
   AND game_date < DATE '2026-08-24';

CREATE INDEX IF NOT EXISTS ncaaf_game_context_season_week
    ON public.ncaaf_game_context (season, season_week);

-- ------------------------------------------------------------
-- Helper functions for the "current week" cutoff
-- ------------------------------------------------------------

-- NFL current week — computed from today's ET date.
-- NFL week rolls forward on Tuesday morning (day after MNF).
CREATE OR REPLACE FUNCTION public.current_nfl_season_week()
RETURNS INTEGER
LANGUAGE SQL
STABLE
AS $$
    WITH today_et AS (
        SELECT (NOW() AT TIME ZONE 'America/New_York')::DATE AS d
    ),
    anchor AS (
        SELECT DATE '2026-09-04' AS wk1_thu
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
    'Returns the current NFL season week (integer). Rolls forward on '
    'Tue/Wed so post-MNF views see the upcoming Thu slate. Client '
    'queries this to know what to filter by.';

CREATE OR REPLACE FUNCTION public.current_ncaaf_season_week()
RETURNS INTEGER
LANGUAGE SQL
STABLE
AS $$
    WITH today_et AS (
        SELECT (NOW() AT TIME ZONE 'America/New_York')::DATE AS d
    ),
    anchor AS (
        SELECT DATE '2026-08-24' AS wk1_start
    ),
    -- NCAAF Wed(3) roll-forward
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

COMMENT ON FUNCTION public.current_ncaaf_season_week() IS
    'Returns the current NCAAF season week. Rolls forward on Tue (post-Sat/Mon slate).';

GRANT EXECUTE ON FUNCTION public.current_nfl_season_week() TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.current_ncaaf_season_week() TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
