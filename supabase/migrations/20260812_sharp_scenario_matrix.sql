-- Sharp scenario matrix (2026-08-12 · sport-universal).
--
-- Comprehensive tracker for every meaningful public/sharp/model
-- pattern combination. Recomputed nightly. Consumed by Jerry synth
-- prompt, refit verdict override, and sweat card composition.
--
-- Categories tracked (extensible — add scenarios by appending rows):
--   money_bucket       money% alone (5 buckets × 2 sides × 2 markets)
--   bets_bucket        bets% alone
--   money_x_bets       2D grid (25 cells per market/side)
--   money_x_model      money band × MC agreement direction
--   money_x_conf       money band × confluence direction
--   whale_divergence   money - bets ≥ 15 by side
--   square_divergence  bets - money ≥ 15 by side
--   rlm_alignment      RLM by direction (fixed sign convention)
--   line_move          open→close delta by direction
--
-- Sport-universal: dispatch keyed on sport column. MLB first;
-- NFL/NCAAF/NHL plug in same schema when their game_context tables
-- populate.
--
-- Populated by:
--   compute_sharp_scenario_matrix.py (nightly, per sport)
--
-- Consumed by:
--   generate_prop_jerry_synthesis.py (prompt context — show Jerry the patterns)
--   generate_jerry_synthesis.py (same)
--   apply_refit_verdict_override.py (auto-adjust conviction when scenario BACKs/FADEs)
--   generate_sweat_card.py (surface high-confidence matches per game)

CREATE TABLE IF NOT EXISTS sharp_scenario_matrix (
  id SERIAL PRIMARY KEY,
  sport TEXT NOT NULL,                    -- MLB / NFL / NCAAF / etc
  market TEXT NOT NULL,                    -- ml / total / spread
  category TEXT NOT NULL,                  -- money_bucket / whale_divergence / etc
  scenario_key TEXT NOT NULL,              -- canonical name
  scenario_label TEXT,                     -- human-readable
  side TEXT,                                -- HOME/AWAY/OVER/UNDER — which side does this key describe
  wins INT NOT NULL DEFAULT 0,
  losses INT NOT NULL DEFAULT 0,
  pushes INT NOT NULL DEFAULT 0,
  total_n INT NOT NULL DEFAULT 0,
  hit_rate NUMERIC(5,2),                   -- 0-100
  roi_pct NUMERIC(6,2),                    -- estimated ROI at -110 baseline
  back_or_fade TEXT,                       -- BACK / FADE / NEUTRAL — actionable label
  jerry_hint TEXT,                         -- BACK / FADE / PASS with confidence
  hint_confidence INT,                     -- 0-100
  window_days INT NOT NULL DEFAULT 90,
  computed_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (sport, market, scenario_key, window_days)
);

CREATE INDEX IF NOT EXISTS idx_ssm_sport_market ON sharp_scenario_matrix (sport, market);
CREATE INDEX IF NOT EXISTS idx_ssm_hint ON sharp_scenario_matrix (jerry_hint, hit_rate DESC);
CREATE INDEX IF NOT EXISTS idx_ssm_actionable ON sharp_scenario_matrix
  (sport, market, back_or_fade) WHERE back_or_fade IN ('BACK', 'FADE');

-- Per-game matches cache (which scenario keys fire for today's slate).
-- Populated same nightly run. App/Jerry read this to see matches per game.
CREATE TABLE IF NOT EXISTS sharp_scenario_game_matches (
  id SERIAL PRIMARY KEY,
  game_id TEXT NOT NULL,
  sport TEXT NOT NULL,
  game_date DATE NOT NULL,
  market TEXT NOT NULL,
  scenario_key TEXT NOT NULL,              -- FK to sharp_scenario_matrix.scenario_key
  side TEXT,                                -- which side of this game triggers
  hit_rate NUMERIC(5,2),                   -- cached hit rate (denormalized for speed)
  n INT,
  back_or_fade TEXT,                       -- BACK/FADE cached
  jerry_hint TEXT,
  hint_confidence INT,
  matched_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (game_id, market, scenario_key)
);

CREATE INDEX IF NOT EXISTS idx_ssgm_game ON sharp_scenario_game_matches (game_id);
CREATE INDEX IF NOT EXISTS idx_ssgm_date_sport ON sharp_scenario_game_matches (sport, game_date DESC);

NOTIFY pgrst, 'reload schema';
