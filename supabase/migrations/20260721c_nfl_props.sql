-- NFL player props — analog of mlb_props.
--
-- Written by nfl_generate_props.py. One row per player × prop_type
-- (pass_yds, rush_yds, reception_yds, receptions, anytime_td) with
-- projection, market line, edge, and tier gate. Resolver fills result
-- post-game.

CREATE TABLE IF NOT EXISTS nfl_props (
  prop_id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  game_id            TEXT NOT NULL,
  game_date          DATE NOT NULL,
  season             INT,
  week               INT,
  home_team          TEXT,
  away_team          TEXT,
  -- Player
  player_id          TEXT,
  player_name        TEXT NOT NULL,
  team               TEXT NOT NULL,             -- player's team (canonical abbrev)
  opponent_team      TEXT NOT NULL,
  position           TEXT,                       -- QB / RB / WR / TE
  -- Prop
  prop_type          TEXT NOT NULL,              -- pass_yds / rush_yds / reception_yds / receptions / anytime_td
  pick_side          TEXT NOT NULL,              -- OVER / UNDER / YES / NO
  pick_line          NUMERIC NOT NULL,
  odds_american      INT,
  -- Model
  projected          NUMERIC,                    -- point projection
  edge               NUMERIC,                    -- projected − line (OVER lens) or negated (UNDER)
  l4_avg             NUMERIC,                    -- 4-game rolling
  season_avg         NUMERIC,
  opp_rank           INT,                        -- opponent defensive rank vs this prop type
  -- Conviction
  tier               TEXT,                       -- PRIME / STRONG / LIGHT / LEAN
  conviction         INT,                        -- 0-100
  signals            JSONB,                      -- freeform breakdown for jerry
  -- Result
  result             TEXT,                       -- W / L / P
  actual_value       NUMERIC,
  resolved_at        TIMESTAMPTZ,
  -- Timestamps
  computed_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (game_id, player_name, prop_type, pick_side)
);

CREATE INDEX IF NOT EXISTS idx_nfl_props_week
  ON nfl_props (season, week);
CREATE INDEX IF NOT EXISTS idx_nfl_props_tier
  ON nfl_props (tier);
CREATE INDEX IF NOT EXISTS idx_nfl_props_result
  ON nfl_props (result) WHERE result IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_nfl_props_player
  ON nfl_props (player_id, season, week);

ALTER TABLE nfl_props ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "nfl_props select all" ON nfl_props;
CREATE POLICY "nfl_props select all"
  ON nfl_props FOR SELECT USING (true);

DROP POLICY IF EXISTS "nfl_props write anon" ON nfl_props;
CREATE POLICY "nfl_props write anon"
  ON nfl_props FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
