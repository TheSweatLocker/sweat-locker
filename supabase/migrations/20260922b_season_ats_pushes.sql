-- 2026-09-22 · Give a push somewhere to live
--
-- Andy, 09-22: "some teams like SEA and NE just have 1-0" — every other
-- team on the Week 3 slate shows two games.
--
-- He is right about both halves. 30 of the 32 Week-3 team slots show
-- two games (2-0, 1-1, 0-2). Exactly two show one: SEA and NE.
--
-- What those two share: NE @ SEA on 09-09 is the ONLY against-the-spread
-- push of the 2026 season. SEA closed -3 and won by exactly 3.
--
-- nfl_game_context tracks `_season_ats_wins` and `_season_ats_losses`
-- and nothing else, and the card renders "{w}-{l} ATS". A push is
-- neither, so the game is not shown as a tie — it is not shown at all.
-- Both teams in that game read "1-0" when they are 1-0-1.
--
-- backfill_nfl_season_records_from_results.py has been COUNTING pushes
-- correctly the whole time:
--
--     elif sr == 'push':
--         agg[home]['ats_pushes'] += 1
--         agg[away]['ats_pushes'] += 1
--
-- and then dropping the number on the floor, because the payload it
-- builds carries only wins, losses and cover_pct. The arithmetic was
-- never wrong; there was nowhere to put the answer.
--
-- team_situational_records already has a pushes column and reports SEA
-- as 0-0-1, so the two surfaces have disagreed about this game since it
-- was played.
--
-- Adding the O/U pushes at the same time. Same writer, same shape, same
-- bug waiting on the first total that lands exactly on the number.
--
-- NCAAF gets the columns too — the client renders both sports through
-- one code path, and a Week-1 FCS game landing on the number would
-- reproduce this exactly.

BEGIN;

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS home_season_ats_pushes INTEGER,
  ADD COLUMN IF NOT EXISTS away_season_ats_pushes INTEGER,
  ADD COLUMN IF NOT EXISTS home_season_ou_pushes  INTEGER,
  ADD COLUMN IF NOT EXISTS away_season_ou_pushes  INTEGER;

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_season_ats_pushes INTEGER,
  ADD COLUMN IF NOT EXISTS away_season_ats_pushes INTEGER,
  ADD COLUMN IF NOT EXISTS home_season_ou_pushes  INTEGER,
  ADD COLUMN IF NOT EXISTS away_season_ou_pushes  INTEGER;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- VERIFY (after re-running backfill_nfl_season_records_from_results.py):
--   SELECT away_team, away_season_ats_wins, away_season_ats_losses,
--          away_season_ats_pushes,
--          home_team, home_season_ats_wins, home_season_ats_losses,
--          home_season_ats_pushes
--     FROM nfl_game_context
--    WHERE game_date >= '2026-09-24' AND (away_team IN ('SEA','NE')
--                                      OR home_team IN ('SEA','NE'));
-- Expect NE 1-0-1 and SEA 1-0-1; every other team 2-0 / 1-1 / 0-2 with
-- pushes = 0.
