-- 2026-09-13 NFL ACTIVE ROSTERS TABLE
-- ================================================================
-- Andy 9/13 caught it: "Cousins plays in vegas what are you talking
-- about" — my KEY PLAYERS aggregator was surfacing Kirk Cousins as
-- ATL QB1 because nfl_player_stats has his rows through 2025 W18
-- team=ATL, no 2026 games logged yet (he moved to LV in the offseason).
-- No table tracks CURRENT-season roster assignments independent of
-- games played. This is the fix.
--
-- Data source: nflverse rosters CSV (github.com/nflverse/nflverse-data/
-- releases/download/rosters/roster_{season}.csv). 2963 rows for 2026,
-- refreshed by nflverse when players sign / get traded / get released.
-- Populated by mlb_pipeline/nfl_rosters_pull.py, cron scheduled weekly
-- on Tuesday before Wed's NFL read regen.
--
-- Unique constraint on (season, team, player_name) so a season-long
-- upsert is idempotent. Depth_chart_position + status let the reads
-- aggregator prefer active starters over reserve/IR/PS players.
-- ================================================================

CREATE TABLE IF NOT EXISTS public.nfl_rosters_current (
    id BIGSERIAL PRIMARY KEY,
    season INTEGER NOT NULL,
    team TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position TEXT,
    depth_chart_position TEXT,
    jersey_number INTEGER,
    status TEXT,
    height TEXT,
    weight INTEGER,
    college TEXT,
    gsis_id TEXT,
    espn_id TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS nfl_rosters_current_uniq
    ON public.nfl_rosters_current (season, team, player_name);

CREATE INDEX IF NOT EXISTS nfl_rosters_current_team_pos
    ON public.nfl_rosters_current (season, team, position);

CREATE INDEX IF NOT EXISTS nfl_rosters_current_player
    ON public.nfl_rosters_current (season, player_name);

COMMENT ON TABLE public.nfl_rosters_current IS
    'NFL active rosters from nflverse. Authoritative source for '
    'player→team mapping in the current season, independent of '
    'games-played data. Refreshed weekly by nfl_rosters_pull.py. '
    'Consumed by generate_nfl_game_reads key-players aggregator to '
    'filter out offseason-move ghosts (Kirk Cousins ATL 2025 → LV 2026 class).';

-- Public read access — same policy as other reference tables
ALTER TABLE public.nfl_rosters_current ENABLE ROW LEVEL SECURITY;

CREATE POLICY nfl_rosters_current_read ON public.nfl_rosters_current
    FOR SELECT USING (TRUE);

GRANT SELECT ON public.nfl_rosters_current TO anon, authenticated;
GRANT ALL ON public.nfl_rosters_current TO service_role;
GRANT USAGE, SELECT ON SEQUENCE nfl_rosters_current_id_seq TO service_role;

NOTIFY pgrst, 'reload schema';
