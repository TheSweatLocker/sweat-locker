-- Scenario audit + line snapshot tables (2026-08-10).
--
-- Two new sport-universal tables that answer: "does public/sharp/house
-- win when scenario X happens?" across every sport we ship.
--
-- ┌──────────────────────────────────────────────────────────────────┐
-- │ line_snapshot                                                    │
-- │                                                                  │
-- │ Time-series of sharp/public/line-move data. One row per poll of  │
-- │ oddscrowd (or equivalent) per (sport, game_id, market). Enables  │
-- │ reverse-line-move detection + full sharp trajectory over time.   │
-- │                                                                  │
-- │ Populated by extending the existing oddscrowd puller to INSERT   │
-- │ instead of overwrite (current mlb_game_context.oddscrowd_snapshot│
-- │ only holds the latest snapshot).                                 │
-- └──────────────────────────────────────────────────────────────────┘
--
-- ┌──────────────────────────────────────────────────────────────────┐
-- │ scenario_audit                                                   │
-- │                                                                  │
-- │ Nightly-recomputed aggregates: hit rate + ROI per unique         │
-- │ (sport, market, scenario_key). The key is a canonical            │
-- │ dimension-tuple string like:                                     │
-- │                                                                  │
-- │   'public_pct=70+&fav=home&spread=1.5&bullpen_taxed=1'           │
-- │                                                                  │
-- │ Sport-agnostic: MLB uses ~50 scenarios (pitcher-park-weather),   │
-- │ NFL will use its own (rest days, coaching), UFC ~15 (weight,     │
-- │ finish rate). All write to the same table with sport tag.        │
-- │                                                                  │
-- │ Downstream: Jerry synth reads matching scenarios to inform       │
-- │ BACK/FADE decisions with real historical priors, not stories.    │
-- └──────────────────────────────────────────────────────────────────┘

CREATE TABLE IF NOT EXISTS public.line_snapshot (
  id BIGSERIAL PRIMARY KEY,
  sport TEXT NOT NULL,                        -- MLB / NFL / UFC / etc.
  game_id TEXT NOT NULL,
  snapshot_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  market TEXT NOT NULL,                       -- ml / spread / total / rl / prop
  -- Money-flow position (may be null if source only reports one side)
  pick_side TEXT,                             -- HOME / AWAY / OVER / UNDER / A / B
  money_pct NUMERIC(5,2),                     -- % of dollars on pick_side
  bets_pct NUMERIC(5,2),                      -- % of bets on pick_side
  divergence NUMERIC(6,2),                    -- money_pct - bets_pct (whale detector)
  -- Line values
  line NUMERIC(6,2),                          -- current line (spread/total)
  odds_pick NUMERIC(6,3),                     -- decimal odds on pick_side
  -- Source
  source TEXT NOT NULL DEFAULT 'oddscrowd',   -- oddscrowd / covers / vsi / manual
  raw JSONB,                                  -- full snapshot for forensic pulls
  UNIQUE (sport, game_id, market, snapshot_ts, source)
);
CREATE INDEX IF NOT EXISTS line_snapshot_game_market_idx
  ON public.line_snapshot (sport, game_id, market, snapshot_ts);
-- 2026-08-10 fix: partial index with NOW() predicate fails (functions in
-- index predicate must be IMMUTABLE). Plain index on snapshot_ts DESC
-- covers recent-window queries adequately; Postgres will BRIN-scan for
-- large ranges anyway.
CREATE INDEX IF NOT EXISTS line_snapshot_ts_idx
  ON public.line_snapshot (snapshot_ts DESC);


CREATE TABLE IF NOT EXISTS public.scenario_audit (
  id BIGSERIAL PRIMARY KEY,
  sport TEXT NOT NULL,
  market TEXT NOT NULL,                       -- ml / spread / total / prop / fight
  scenario_key TEXT NOT NULL,                 -- canonical dim-tuple (see above)
  scenario_label TEXT,                        -- human-readable (e.g. "Public 70%+ on home fav ML")
  -- Aggregates over the scenario_window
  wins INT NOT NULL DEFAULT 0,
  losses INT NOT NULL DEFAULT 0,
  pushes INT NOT NULL DEFAULT 0,
  total_n INT NOT NULL DEFAULT 0,
  hit_rate NUMERIC(5,2),                      -- wins / (wins + losses) * 100
  avg_dec_odds NUMERIC(6,3),                  -- for ROI calc
  roi_pct NUMERIC(6,2),                       -- juice-adjusted
  -- Interpretation hints
  jerry_hint TEXT,                            -- BACK / FADE / PASS / SIGNAL
  hint_confidence INT,                        -- 0-100
  -- Metadata
  scenario_window TEXT NOT NULL DEFAULT 'lifetime',  -- lifetime / 90d / 30d / time_weighted
  first_seen DATE,
  last_seen DATE,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (sport, market, scenario_key, scenario_window)
);
CREATE INDEX IF NOT EXISTS scenario_audit_sport_market_idx
  ON public.scenario_audit (sport, market, hit_rate DESC);
CREATE INDEX IF NOT EXISTS scenario_audit_actionable_idx
  ON public.scenario_audit (sport, jerry_hint, roi_pct DESC)
  WHERE total_n >= 20 AND jerry_hint IN ('BACK', 'FADE');


-- RLS: read-only for anon (matches other pipeline-output tables)
ALTER TABLE public.line_snapshot ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.line_snapshot;
CREATE POLICY public_read ON public.line_snapshot FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.line_snapshot;
CREATE POLICY public_write ON public.line_snapshot FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

ALTER TABLE public.scenario_audit ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.scenario_audit;
CREATE POLICY public_read ON public.scenario_audit FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.scenario_audit;
CREATE POLICY public_write ON public.scenario_audit FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
