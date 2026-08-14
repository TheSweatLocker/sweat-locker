-- Steam Room Ladder (2026-08-13).
--
-- Persist the ladder's state + history. Two tables:
--
-- ladder_rung — every qualifier the engine has ever surfaced, with
--   its criteria snapshot + eventual result. Streak counting reads from
--   here (WIN rows in a row = current streak). Reset on LOSS.
--
-- ladder_state — the current active rung the app shows in the Steam
--   Room. Exactly ONE row (id=1). Rewritten by the engine when a new
--   qualifier fires; nulled when engine finds no qualifier (WAITING
--   state); rewritten with RESET after a loss grades.
--
-- Ladder discipline:
--   * Fires only when a play clears ALL gates (PRIME/STRONG, cohort ≥60%
--     n≥30, edge ≥10pp, consensus 4/5, sides or 5-signal totals only).
--   * Skips freely — 2x/week minimum via self-tuning (gate loosens if
--     rungs/week drops below 2 for 2 consecutive weeks).
--   * Universal scope — any live sport with ladder_eligible=true in
--     sport_registry.

CREATE TABLE IF NOT EXISTS public.ladder_rung (
  id             BIGSERIAL PRIMARY KEY,
  qualified_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  game_date      DATE NOT NULL,
  sport          TEXT NOT NULL,
  game_id        TEXT NOT NULL,
  matchup        TEXT NOT NULL,
  pick_side      TEXT NOT NULL,        -- e.g. "Milwaukee Brewers ML" | "Under 8.5"
  market         TEXT NOT NULL,        -- 'ml' | 'spread' | 'total'
  odds_american  INT,                  -- captured at qualification
  tier           TEXT NOT NULL,        -- 'PRIME' | 'STRONG'
  conviction     INT,
  model_win_prob NUMERIC,              -- 0-100
  cohort_hit_rate NUMERIC,             -- 0-100
  cohort_n       INT,
  consensus_lens INT,                  -- how many lens agreed (4/5, 5/5)
  edge_pp        NUMERIC,              -- model_win_prob - implied_prob
  qualification_notes TEXT,            -- why it qualified (audit)
  result         TEXT,                 -- 'Win' | 'Loss' | 'Push' | NULL (pending)
  resolved_at    TIMESTAMPTZ,
  UNIQUE (game_id, market, pick_side, qualified_at)
);

CREATE INDEX IF NOT EXISTS ladder_rung_qualified_idx
  ON public.ladder_rung (qualified_at DESC);
CREATE INDEX IF NOT EXISTS ladder_rung_resolved_idx
  ON public.ladder_rung (resolved_at DESC) WHERE result IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.ladder_state (
  id             INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
  status         TEXT NOT NULL DEFAULT 'waiting'
                 CHECK (status IN ('active', 'waiting', 'reset')),
  active_rung_id BIGINT REFERENCES public.ladder_rung(id),
  current_streak INT NOT NULL DEFAULT 0,
  longest_streak INT NOT NULL DEFAULT 0,
  last_result    TEXT,                 -- 'Win' | 'Loss' | 'Push' | NULL
  last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  note           TEXT
);

-- Ensure the singleton row exists on migration apply
INSERT INTO public.ladder_state (id, status, note)
VALUES (1, 'waiting', 'Ladder initialized — waiting for first qualifier')
ON CONFLICT (id) DO NOTHING;

-- RLS: readable by anon; writable by pipeline (matches sibling tables)
ALTER TABLE public.ladder_rung  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ladder_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read ON public.ladder_rung;
CREATE POLICY public_read ON public.ladder_rung
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.ladder_rung;
CREATE POLICY public_write ON public.ladder_rung
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS public_read ON public.ladder_state;
CREATE POLICY public_read ON public.ladder_state
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.ladder_state;
CREATE POLICY public_write ON public.ladder_state
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
