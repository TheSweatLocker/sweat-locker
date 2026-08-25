-- The Ledger — auto-suggested chalk parlays + teasers (2026-08-17).
--
-- Backend generator (generate_ledger.py) picks 2-3 combos per day
-- from today's ensemble picks across MLB/NFL/NCAAF/NCAAB/UFC. App
-- renders as Steam Room 4th sub-tab, user can edit legs.
--
-- Kinds:
--   'chalk_parlay' — 2-3 ML favorites combined for +100 to +150 payout
--   'teaser'       — line moved into higher-prob zone (Over 8.0 → 6.5,
--                    Spread -9 → -5), paired with correlated leg for
--                    even money math

CREATE TABLE IF NOT EXISTS public.ledger_suggestions (
  id              BIGSERIAL PRIMARY KEY,
  game_date       DATE NOT NULL,
  kind            TEXT NOT NULL,        -- 'chalk_parlay' | 'teaser'
  sport_scope     TEXT NOT NULL,        -- 'MLB' | 'NFL' | 'MULTI'
  legs            JSONB NOT NULL,       -- see leg schema below
  combined_odds   INT,                  -- american +/- payout
  combined_prob   NUMERIC,              -- estimated hit probability
  reasoning       TEXT,                 -- Jerry-style writeup
  rank            INT NOT NULL DEFAULT 0, -- display order (1 = top)
  auto_generated  BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- legs JSONB shape:
-- [
--   {
--     "sport": "MLB",
--     "matchup": "Miami Marlins @ Philadelphia Phillies",
--     "market": "total",              -- 'ml' | 'spread' | 'total'
--     "pick": "Under 8.0",
--     "original_odds": -110,
--     "original_line": 8.0,
--     "teased_line": 6.5,             -- null for chalk_parlay legs
--     "teased_odds": -280,            -- null for chalk_parlay legs
--     "tier": "STRONG",
--     "conviction": 66,
--     "game_id": "..."
--   },
--   ...
-- ]

CREATE INDEX IF NOT EXISTS idx_ledger_date_kind ON public.ledger_suggestions (game_date DESC, kind);
CREATE INDEX IF NOT EXISTS idx_ledger_created ON public.ledger_suggestions (created_at DESC);

-- Resolution tracking (added post-launch for hit-rate accountability)
ALTER TABLE public.ledger_suggestions
  ADD COLUMN IF NOT EXISTS result TEXT,                -- 'Win' | 'Loss' | 'Push' | null (pending)
  ADD COLUMN IF NOT EXISTS legs_resolved JSONB,        -- per-leg outcomes
  ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

NOTIFY pgrst, 'reload schema';
