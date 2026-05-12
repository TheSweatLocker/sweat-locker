-- NBA conviction-tier picks (Phase 2 migration)
-- ============================================================
-- Mirrors the MLB Pipeline Props pattern but at game level (ML/ATS/total)
-- rather than player-prop level. Server-side generator (nba_picks_generator.py)
-- runs daily, computes model-vs-market edge with chalk-trap detector,
-- assigns PRIME/STRONG/LEAN tier, and writes to this table. App reads
-- via standard select; resolver fills result post-game.
--
-- Why per-game (not per-prop) for NBA: we don't have NBA player-prop pipeline
-- data yet (Phase 3 work). NBA team data IS robust (net rating, pace, eFG%,
-- pace-adjusted projections) — enough for game-level picks today.
--
-- Apply via Supabase SQL editor.

CREATE TABLE IF NOT EXISTS nba_game_picks (
  id BIGSERIAL PRIMARY KEY,
  game_id            TEXT NOT NULL,
  game_date          DATE NOT NULL,
  season             TEXT,                          -- "2025-26"
  home_team          TEXT NOT NULL,
  away_team          TEXT NOT NULL,

  -- Pick details
  pick_type          TEXT NOT NULL,                 -- 'ml' | 'ats' | 'total' | 'star_out_skip'
  pick_side          TEXT,                          -- 'home' | 'away' | 'over' | 'under' | null for skip
  pick_label         TEXT,                          -- "Lakers dog cover" / "Over 220.5" — what shows in app
  conviction         INTEGER,                       -- 0-100
  tier               TEXT,                          -- 'PRIME' | 'STRONG' | 'LEAN' | null

  -- Model + market state at pick time (snapshot)
  net_rating_gap     NUMERIC,                       -- home - away
  market_spread      NUMERIC,                       -- close spread (home perspective, neg = home favored)
  model_edge         NUMERIC,                       -- nrGap + market_spread
  pace_avg           NUMERIC,                       -- (home_pace + away_pace) / 2
  projected_total    NUMERIC,                       -- pace × efficiency projection
  market_total       NUMERIC,                       -- close total
  total_edge         NUMERIC,                       -- projected - market

  -- ML odds at pick time
  home_ml            INTEGER,
  away_ml            INTEGER,

  -- Recency context
  home_drift         NUMERIC,                       -- L10 - season net rating
  away_drift         NUMERIC,
  home_injury_note   TEXT,
  away_injury_note   TEXT,

  -- Explanation
  signals            JSONB DEFAULT '{}'::jsonb,     -- supporting context strings

  -- Outcome (filled by resolver)
  result             TEXT,                          -- 'Win' | 'Loss' | 'Push' | 'Pending' | null
  final_value        NUMERIC,                       -- spread margin / total points / etc
  resolved_at        TIMESTAMPTZ,

  created_at         TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (game_id, pick_type, pick_side)
);

CREATE INDEX IF NOT EXISTS idx_nba_picks_date ON nba_game_picks(game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nba_picks_tier ON nba_game_picks(tier, game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nba_picks_unresolved
  ON nba_game_picks(game_date) WHERE result IS NULL;
