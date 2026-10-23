-- == 2026-10-22 · NBA RESULTS NEED TO SAY WHICH SEASON PHASE THEY ARE ==
--
-- Every team record on an NBA game card counts preseason exhibitions, the
-- All-Star Game and the playoffs, because nothing in the data says which is
-- which.
--
-- ── THE MEASUREMENT ─────────────────────────────────────────────────────
-- team_situational_records, season 2025-26, market=ml, filter=overall:
--
--     ALL 30 of 30 teams exceed 82 games.   range 84..110
--     home filter 42..54 against a real 41. 30 of 30 over
--     road filter 42..56 against a real 41. 30 of 30 over
--
-- Listing distinct teams in nba_game_results returns THIRTY-SEVEN, not
-- thirty:
--
--     Guangzhou Loong-Lions, Melbourne United, Hapoel Jerusalem,
--     Melbourne Pnx, London Lions          <- preseason exhibitions
--     Team Stars, Team Stripes, World,
--     Team Chuck/Kenny/Shaq/Candace        <- All-Star Game
--     games dated through 2026-06-13       <- playoffs
--
-- Even Washington, a lottery team, shows 85 games: 82 + 3 preseason.
--
-- It is not an NBA-only bug. Same query, other sports, teams over a possible
-- season length: NHL 2025 16 of 32, MLB 2026 7 of 30, NCAAB 45 of 365. The
-- matview (20260901e) filters on `WHERE home_score IS NOT NULL` and nothing
-- else, for every sport.
--
-- ── WHY ONLY NBA IN THIS MIGRATION ──────────────────────────────────────
-- Because NBA is the only one of the four where the fix can be EXACT.
-- nba_game_results.game_id is the ESPN event id ('401909101'), so ESPN's
-- own season.type joins on the primary key with no name matching at all.
--
-- The others cannot be joined that way and must not be bodged into it:
--     ncaab_game_results.game_id = 'ncaab_20260407_Connecticut_Michigan'
--     mlb_game_results.game_id   = 'be50f6c59c20efa430eec6f146a0fcb1'
-- Matching either against ESPN means joining on team NAMES across two
-- feeds, which is exactly what cost STL every odd and every tendency for
-- weeks (project_nhl_stl_name_mismatch_1013). They get their own migration
-- with their own join, once that join is established and verified.
--
-- Adding the column to all four now and backfilling only one would be worse
-- than this: a NULL would mean both "not yet backfilled" and "unknown
-- phase", and the matview could not tell them apart.
--
-- ── WHY A COLUMN AND NOT A DATE WINDOW ──────────────────────────────────
-- A per-season date boundary was the tempting shortcut and it is guesswork:
-- preseason, regular season and playoffs overlap at their edges (an April
-- date holds both week-82 regular-season games and first-round playoff
-- games), and sport_registry carries boundaries for the CURRENT season only
-- (NBA 2026-10-20), so a prior season has nothing to bound it with.
--
-- ESPN states the phase per event. Stored, it is checkable forever and does
-- not drift.
--
-- NULL is left meaning "not yet determined" on purpose. The matview is NOT
-- changed in this migration: the filter goes in only after the backfill
-- reports full coverage, because a filter applied to a half-populated
-- column would silently delete most of the records instead of correcting
-- them.

ALTER TABLE public.nba_game_results
    ADD COLUMN IF NOT EXISTS season_type TEXT;

COMMENT ON COLUMN public.nba_game_results.season_type IS
    'preseason | regular | postseason | allstar, from ESPN season.type '
    '(1/2/3) joined on game_id = ESPN event id. NULL = not yet backfilled. '
    'Added 2026-10-22 because team records counted preseason, the All-Star '
    'Game and playoffs - all 30 teams showed more than 82 games.';

--: The matview groups by season and will filter by phase, and the backfill
--: walks by date. Both are covered here.
CREATE INDEX IF NOT EXISTS nba_game_results_season_type_idx
    ON public.nba_game_results (season, season_type);

NOTIFY pgrst, 'reload schema';
