-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-03 · NHL puck-line + total prices ON THE CONTEXT TABLE
--
-- WHY
-- ---
-- 20261001a added these four columns to nhl_game_results and wired
-- nhl_odds_pull to fill them. That worked: on 2026-10-03 all 13 forward
-- games carry a real puck-line price (Buffalo -1.5 at +110/-130, Pittsburgh
-- +1.5 at -234/+192, and so on), plus both total prices.
--
-- But nothing reads nhl_game_results at pick time. The scorer, the
-- defensive gates, the Sharp Card and the Sweat Card all read
-- nhl_game_context — and that table has NO price columns at all. So the
-- numbers we fetch, store and pay for are invisible to every consumer that
-- could act on them.
--
-- THIS IS THE ACTUAL BLOCKER ON THE NHL rl MARKET, and it was misdiagnosed
-- twice. project_nhl_market_race_1002 recorded "rl BLOCKED on puck-line
-- prices we don't store" — we do store them. Earlier today I told Andy the
-- prices were "present 0/13, an upstream posting gap" — they are present
-- 13/13, one table over. Both readings came from querying the context table
-- and concluding the data did not exist.
--
-- That matters because rl holds the only VALIDATED weight-1.0 signals in the
-- entire NHL stack (home_ats_cold_at_home 66.1% n=59 edge +13.7,
-- home_team_ats_cold 62.7% n=51 edge +10.3), and the market has produced
-- essentially no picks. A signal set cannot be assessed against a price the
-- scorer cannot see.
--
-- Mirrors 20261001a exactly: same four names, same types, NHL only. The
-- enrich_market step in nhl_game_context.py copies them across from results
-- in the same pass that already copies close_total and close_puckline.
--
-- FORWARD-ONLY, same as 20261001a. Historical context rows stay NULL; we
-- have no historical puck-line odds source. Do NOT read NULL here as "no
-- juice" — read it as "unknown", which is what apply_unpriced_market_gate
-- already does for a missing moneyline.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.nhl_game_context
  ADD COLUMN IF NOT EXISTS close_puckline_home_price integer,
  ADD COLUMN IF NOT EXISTS close_puckline_away_price integer,
  ADD COLUMN IF NOT EXISTS close_total_over_price    integer,
  ADD COLUMN IF NOT EXISTS close_total_under_price   integer;

COMMENT ON COLUMN public.nhl_game_context.close_puckline_home_price IS
  'American odds on the HOME side of close_puckline. Copied from '
  'nhl_game_results by enrich_market. Forward-only from 2026-10-03.';
COMMENT ON COLUMN public.nhl_game_context.close_puckline_away_price IS
  'American odds on the AWAY side of close_puckline. Copied from '
  'nhl_game_results by enrich_market. Forward-only from 2026-10-03.';
COMMENT ON COLUMN public.nhl_game_context.close_total_over_price IS
  'American odds on OVER at close_total. Copied from nhl_game_results.';
COMMENT ON COLUMN public.nhl_game_context.close_total_under_price IS
  'American odds on UNDER at close_total. Copied from nhl_game_results.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — read it back, do not trust the DDL succeeding. A 204 on a write
-- to this table has already lied once this week (see the pick-lock trigger
-- in 20260929d), so count the populated rows rather than the statements.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT count(*)                           AS rows,
--        count(close_puckline)               AS have_pl_line,
--        count(close_puckline_home_price)    AS have_pl_home_px,
--        count(close_puckline_away_price)    AS have_pl_away_px,
--        count(close_total_over_price)       AS have_tot_over_px
--   FROM public.nhl_game_context
--  WHERE game_date >= '2026-10-03';
