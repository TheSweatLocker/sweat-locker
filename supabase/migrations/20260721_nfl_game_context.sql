-- NFL game context — analog of mlb_game_context.
--
-- Written by nfl_game_context.py per Odds API pull. Serves as source of
-- truth for the app's NFL game detail modal + weekly card + POTD.
--
-- Naming: mirrors mlb_game_context / ncaab_game_context. context = pick-time
-- state (pre-game snapshot); results = resolved outcomes.
--
-- Sign convention: matches nflverse standard (close_spread > 0 = home fav).
-- projected_spread same convention.

CREATE TABLE IF NOT EXISTS nfl_game_context (
  game_id                       TEXT PRIMARY KEY,      -- Odds API event ID
  game_date                     DATE NOT NULL,
  season                        INT,
  season_type                   TEXT DEFAULT 'REG',
  week                          INT,
  home_team                     TEXT NOT NULL,         -- canonical (KC, PHI, ...)
  away_team                     TEXT NOT NULL,
  kickoff_utc                   TIMESTAMPTZ,
  -- Market lines (Odds API, home-team perspective, nflverse convention)
  close_spread                  NUMERIC,               -- +3.5 = home favored 3.5
  open_spread                   NUMERIC,
  close_total                   NUMERIC,
  open_total                    NUMERIC,
  close_home_ml                 INT,
  close_away_ml                 INT,
  -- Weather / venue
  roof                          TEXT,                  -- 'outdoors' | 'dome' | 'closed' | 'open'
  surface                       TEXT,                  -- 'grass' | 'turf'
  temp                          INT,
  wind                          INT,
  -- Rest / travel
  home_rest                     INT,
  away_rest                     INT,
  div_game                      BOOLEAN,
  -- Model projections (EPA-based)
  home_off_rating               NUMERIC,               -- (pass_epa + rush_epa) / games
  away_off_rating               NUMERIC,
  power_diff                    NUMERIC,               -- home_rating - away_rating
  projected_spread              NUMERIC,               -- positive = home fav
  projected_total               NUMERIC,
  model_pred_home_points        NUMERIC,
  model_pred_away_points        NUMERIC,
  -- Signal confluence (analog of MLB / NCAAB)
  signal_confluence_net         INT,                   -- positive = home lean
  signal_confluence_breakdown   JSONB,
  -- Cohort tags computed at pick time
  cohort_tags                   TEXT[],
  -- Sweat score (0-100, universal tier bands)
  sweat_score                   INT,
  sweat_tier                    TEXT,                  -- PRIME/STRONG/LIGHT_LEAN/PASS
  -- Primary play (structured)
  primary_play                  JSONB,
  -- Timestamps
  computed_at                   TIMESTAMPTZ DEFAULT NOW(),
  updated_at                    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nfl_game_context_game_date
  ON nfl_game_context (game_date DESC);

CREATE INDEX IF NOT EXISTS idx_nfl_game_context_week
  ON nfl_game_context (season, week);

-- RLS: match nfl_game_results policy — pipeline writes via anon (SUPABASE_KEY)
ALTER TABLE nfl_game_context ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "nfl_game_context select all" ON nfl_game_context;
CREATE POLICY "nfl_game_context select all"
  ON nfl_game_context FOR SELECT USING (true);

DROP POLICY IF EXISTS "nfl_game_context write anon" ON nfl_game_context;
CREATE POLICY "nfl_game_context write anon"
  ON nfl_game_context FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
