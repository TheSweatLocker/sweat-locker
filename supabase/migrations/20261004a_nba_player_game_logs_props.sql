-- 2026-10-04a · NBA player game logs — the columns props actually need
--
-- WHY
-- NBA's regular season opens 2026-10-21 (sport_registry) and we serve
-- nothing for it. Sides and totals were tested first and have NO edge
-- (actual_margin ~ +1.058*market -0.015*projection, ATS 51.1% on n=2,203
-- against a 52.4% breakeven), so the NBA opportunity is PROPS — our
-- highest-volume surface and the one where 99.8% of receipts carry a price.
--
-- The blocker: nba_player_game_logs has ZERO rows, and the columns it does
-- have cannot support a prop board:
--
--   present: game_id, game_date, player_id, player_name, team_abbrev,
--            minutes, points, rebounds, assists, steals, blocks,
--            turnovers, plus_minus
--   MISSING: threes made/attempted  <- a top-3 NBA prop market
--            opponent, home/away    <- every matchup and home-road lens
--            shooting splits, started
--
-- Without opponent and is_home, the matchup and home/road prop lenses are
-- structurally dead at sample_n = 0 — the same failure already documented
-- for NFL ("nfl_player_stats has no home/away column — deferred") and for
-- NHL before api-web.nhle.com replaced the dead ESPN path.
--
-- SOURCE is ESPN's summary endpoint, verified live 2026-10-04:
--   site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>
-- and our nba_game_results.game_id IS the ESPN event id (401902644 ->
-- MIA 129 @ TOR 105, 2026-10-03). It returns exactly these labels per
-- player: MIN PTS FG 3PT FT REB AST TO STL BLK OREB DREB PF +/-.
-- balldontlie returns 401 (BDL_API_KEY unset) and stats.nba.com times out,
-- so ESPN is the only working path.
--
-- Idempotent — safe to re-run.

ALTER TABLE nba_player_game_logs
    ADD COLUMN IF NOT EXISTS season           TEXT,
    ADD COLUMN IF NOT EXISTS opponent_abbrev  TEXT,
    ADD COLUMN IF NOT EXISTS is_home          BOOLEAN,
    ADD COLUMN IF NOT EXISTS started          BOOLEAN,
    ADD COLUMN IF NOT EXISTS fg3m             INTEGER,
    ADD COLUMN IF NOT EXISTS fg3a             INTEGER,
    ADD COLUMN IF NOT EXISTS fgm              INTEGER,
    ADD COLUMN IF NOT EXISTS fga              INTEGER,
    ADD COLUMN IF NOT EXISTS ftm              INTEGER,
    ADD COLUMN IF NOT EXISTS fta              INTEGER,
    ADD COLUMN IF NOT EXISTS oreb             INTEGER,
    ADD COLUMN IF NOT EXISTS dreb             INTEGER,
    ADD COLUMN IF NOT EXISTS pf               INTEGER,
    ADD COLUMN IF NOT EXISTS ingested_at      TIMESTAMPTZ DEFAULT now();

COMMENT ON COLUMN nba_player_game_logs.fg3m IS
  'Three-pointers MADE. Top-3 NBA prop market; the whole reason this migration exists.';
COMMENT ON COLUMN nba_player_game_logs.opponent_abbrev IS
  'Opponent, so the matchup prop lens has a key to join on. Without it that lens sits at sample_n=0 and reads as unvalidated rather than absent.';
COMMENT ON COLUMN nba_player_game_logs.is_home IS
  'TRUE if the player''s team was at home. ESPN gives this for free per box score; NFL still cannot supply it.';
COMMENT ON COLUMN nba_player_game_logs.minutes IS
  'Minutes played as an integer. ESPN reports DNPs with an empty stat line — those rows are skipped by the ingester rather than written as 0, because a 0-minute row and a did-not-play row mean different things to an L10 average.';

-- Idempotent ingestion key. Without this, a re-run duplicates every row and
-- an L10 average silently double-counts games.
CREATE UNIQUE INDEX IF NOT EXISTS nba_player_game_logs_game_player_uq
    ON nba_player_game_logs (game_id, player_id);

-- Lookback queries are always "this player, before this date, most recent N".
CREATE INDEX IF NOT EXISTS nba_player_game_logs_player_date_idx
    ON nba_player_game_logs (player_id, game_date DESC);

CREATE INDEX IF NOT EXISTS nba_player_game_logs_name_date_idx
    ON nba_player_game_logs (player_name, game_date DESC);

NOTIFY pgrst, 'reload schema';
