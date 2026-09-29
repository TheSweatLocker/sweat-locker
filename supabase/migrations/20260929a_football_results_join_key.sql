-- ════════════════════════════════════════════════════════════════════════
-- 20260929a · Football context → results join key
--
-- WHY
-- ---
-- nfl_game_context.game_id is an Odds-API hex id. nfl_game_results.game_id is
-- '20260927_BAL_DAL'. Measured 2026-09-29: the two sets have ZERO intersection,
-- and the same split exists for NCAAF. This is project_nfl_game_id_mismatch_911,
-- still open since 09-11.
--
-- Consequence: nothing can join a PICK to its RESULT. compute_surface_records
-- reports 35 graded NFL sides when the real number is 47, and the full football
-- engine audit had to reconstruct the join from (game_date, away_team,
-- home_team) by hand in a throwaway script. Every future measurement would need
-- to repeat that, and the next person to write `JOIN ... USING (game_id)` gets
-- silence rather than an error.
--
-- WHAT
-- ----
-- A STORED GENERATED column on each context table that reproduces the results
-- table's own id from columns the context table already holds. Verified against
-- live data before writing this:
--
--     nfl_game_context    242 of 242 result ids resolve (100%)
--     ncaaf_game_context  385 resolve; the 36 that do not are FCS opponents
--                         and games genuinely absent from results
--
-- GENERATED, not a plain column with a trigger or a backfill, because it then
-- cannot drift: no writer has to remember it, and it is correct for rows that
-- already exist the moment this runs.
--
-- WHY THE ODD DATE EXPRESSION
-- ---------------------------
-- to_char(date, 'YYYYMMDD') is STABLE, not IMMUTABLE — Postgres rejects it in a
-- generated column. extract() on a date IS immutable, so the key is assembled
-- from extracted parts via a small IMMUTABLE helper. That helper is also the one
-- place to change if the id format ever moves.
--
-- NOT TOUCHED
-- -----------
-- Neither game_id. Both are primary keys with rows hanging off them, and the
-- point is to bridge the two schemes, not to pick a winner.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

-- IMMUTABLE so it is legal inside a generated column. extract() on date is
-- immutable; to_char() is not.
CREATE OR REPLACE FUNCTION public.sl_date_key(d date)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
  SELECT lpad(extract(year  from d)::int::text, 4, '0')
      || lpad(extract(month from d)::int::text, 2, '0')
      || lpad(extract(day   from d)::int::text, 2, '0')
$$;

COMMENT ON FUNCTION public.sl_date_key(date) IS
  'YYYYMMDD from a date. IMMUTABLE so generated columns can use it; to_char is only STABLE.';

-- ── NFL ─────────────────────────────────────────────────────────────────
-- nfl_game_results.game_id == '20260927_BAL_DAL'
ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS results_game_id text
  GENERATED ALWAYS AS (
    public.sl_date_key(game_date) || '_' || away_team || '_' || home_team
  ) STORED;

COMMENT ON COLUMN public.nfl_game_context.results_game_id IS
  'Join key to nfl_game_results.game_id. game_id here is the Odds-API event id '
  'and does not intersect the results table at all — see 20260929a.';

CREATE INDEX IF NOT EXISTS idx_nfl_ctx_results_game_id
  ON public.nfl_game_context (results_game_id);

-- ── NCAAF ───────────────────────────────────────────────────────────────
-- ncaaf_game_results.game_id == 'ncaaf_20260829_Hawaii_Stanford'
-- Full team names with spaces, deliberately matching that table.
ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS results_game_id text
  GENERATED ALWAYS AS (
    'ncaaf_' || public.sl_date_key(game_date) || '_' || away_team || '_' || home_team
  ) STORED;

COMMENT ON COLUMN public.ncaaf_game_context.results_game_id IS
  'Join key to ncaaf_game_results.game_id. See 20260929a.';

CREATE INDEX IF NOT EXISTS idx_ncaaf_ctx_results_game_id
  ON public.ncaaf_game_context (results_game_id);

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — expect nfl_resolved = the full nfl_game_results row count for the
-- season, and ncaaf_resolved ≈ 385. A zero on either side means the id format
-- moved and sl_date_key or the concatenation needs updating.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT 'NFL' AS sport,
--        count(*) FILTER (WHERE r.game_id IS NOT NULL) AS resolved,
--        count(*)                                      AS ctx_rows
--   FROM public.nfl_game_context c
--   LEFT JOIN public.nfl_game_results r ON r.game_id = c.results_game_id
-- UNION ALL
-- SELECT 'NCAAF',
--        count(*) FILTER (WHERE r.game_id IS NOT NULL),
--        count(*)
--   FROM public.ncaaf_game_context c
--   LEFT JOIN public.ncaaf_game_results r ON r.game_id = c.results_game_id;
