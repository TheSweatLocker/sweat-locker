-- NFL game picks — analog of nba_game_picks / mlb picks tables.
--
-- Written by nfl_play_of_day.py after nfl_game_context. One row per
-- (game_id, pick_type, pick_side) tier-gated selection. lock_of_week
-- flag marks the single anchor pick that surfaces at top of the app's
-- weekly card and drives the ladder / weekly parlay math.

CREATE TABLE IF NOT EXISTS nfl_game_picks (
  pick_id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  game_id            TEXT NOT NULL,             -- FK to nfl_game_context.game_id (loose)
  game_date          DATE NOT NULL,
  season             INT,
  week               INT,
  season_type        TEXT DEFAULT 'REG',
  home_team          TEXT NOT NULL,
  away_team          TEXT NOT NULL,
  -- Pick shape
  pick_type          TEXT NOT NULL,             -- 'spread' | 'total' | 'ml' | 'skip'
  pick_side          TEXT NOT NULL,             -- 'home' | 'away' | 'over' | 'under' | 'skip'
  pick_label         TEXT,                      -- human-readable ("Panthers +7", "Under 44.5")
  pick_line          NUMERIC,                   -- the spread/total number
  odds_american      INT,
  tier               TEXT,                      -- PRIME/STRONG/LIGHT/LEAN
  conviction         INT,                       -- 0-100 (mirrors sweat_score band)
  cohort_tags        TEXT[],                    -- audit cohort membership
  -- Model context (denormalized for fast query in app + resolver)
  projected_spread   NUMERIC,
  projected_total    NUMERIC,
  close_spread       NUMERIC,
  close_total        NUMERIC,
  spread_edge        NUMERIC,                   -- projected − close (spread cover picks)
  total_edge         NUMERIC,
  signal_confluence  INT,
  signals            JSONB,                     -- freeform breakdown for jerry read
  -- Weekly anchor flag
  is_lock_of_week    BOOLEAN DEFAULT FALSE,
  -- Result (resolver fills post-game)
  result             TEXT,                      -- 'W' | 'L' | 'P' | NULL
  actual_spread      NUMERIC,
  actual_total       NUMERIC,
  resolved_at        TIMESTAMPTZ,
  -- Timestamps
  computed_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (game_id, pick_type, pick_side)
);

CREATE INDEX IF NOT EXISTS idx_nfl_game_picks_week
  ON nfl_game_picks (season, week);
CREATE INDEX IF NOT EXISTS idx_nfl_game_picks_tier
  ON nfl_game_picks (tier, is_lock_of_week);
CREATE INDEX IF NOT EXISTS idx_nfl_game_picks_result
  ON nfl_game_picks (result) WHERE result IS NOT NULL;

ALTER TABLE nfl_game_picks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "nfl_game_picks select all" ON nfl_game_picks;
CREATE POLICY "nfl_game_picks select all"
  ON nfl_game_picks FOR SELECT USING (true);

DROP POLICY IF EXISTS "nfl_game_picks write anon" ON nfl_game_picks;
CREATE POLICY "nfl_game_picks write anon"
  ON nfl_game_picks FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
