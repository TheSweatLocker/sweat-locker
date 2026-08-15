-- Line movement intelligence — sport-universal (2026-08-15).
--
-- Existing infra reused:
--   * line_history (per-book time-series snapshots)
--   * line_snapshot (OddsCrowd bets%/money%/divergence time-series)
--   * fadereport_signals (fadereport public splits, per 20260814_fadereport_signals)
--   * line_movement_flags (steam/RLM/limit patterns already detected)
--   * <sport>_game_context.open_* / close_* columns
--
-- New this migration:
--
-- 1. line_movement_flags.classification — sharp/public/RLM/steam tag
--    emitted by classify_line_moves.py after cross-referencing the line
--    move against oddscrowd + fadereport public splits. Turns "line moved
--    0.5pts" from noise into a real classified signal.
--
-- 2. line_movement_flags.money_pct / bets_pct — snapshot the split at time
--    of classification so we can audit the decision after the fact.
--
-- 3. <sport>_game_context.close_locked_at — timestamp at which the true
--    closing line was frozen by freeze_closing_lines.py. Distinguishes
--    "close_total was set by whatever pull ran most recently" from
--    "close_total is the actual line at first pitch minus 15 min."
--    Non-null = true close; null = still polling.
--
-- 4. clv_snapshots — per-pick CLV (closing line value). Recorded after
--    grading. clv > 0 means we beat the close; clv < 0 means the market
--    moved against our pick. Best proxy for pick quality that's not
--    graded win/loss (which has variance noise).
--
-- CLV convention:
--   spread pick: clv = (our_spread_number - close_spread_number) * direction
--                  where direction = +1 if we backed the fav, -1 if dog
--   total pick:  clv = (close_total - our_total) if OVER
--                        (our_total - close_total) if UNDER
--   ml pick:     clv = decimal_odds_our - decimal_odds_close  (positive = we got better price)

-- ─── #1 · Classification + split snapshot on line_movement_flags ───────

ALTER TABLE public.line_movement_flags
  ADD COLUMN IF NOT EXISTS classification TEXT,
  ADD COLUMN IF NOT EXISTS money_pct      NUMERIC,
  ADD COLUMN IF NOT EXISTS bets_pct       NUMERIC,
  ADD COLUMN IF NOT EXISTS bettors_pct    NUMERIC,
  ADD COLUMN IF NOT EXISTS handle_pct     NUMERIC,
  ADD COLUMN IF NOT EXISTS classified_at  TIMESTAMPTZ;

COMMENT ON COLUMN public.line_movement_flags.classification IS
  'SHARP_MOVE | PUBLIC_MOVE | RLM | STEAM | CONSENSUS | NEUTRAL — emitted by classify_line_moves.py';
COMMENT ON COLUMN public.line_movement_flags.money_pct IS
  'OddsCrowd money% on the side line moved TO, at time of classification';
COMMENT ON COLUMN public.line_movement_flags.bets_pct IS
  'OddsCrowd bets% on the side line moved TO, at time of classification';
COMMENT ON COLUMN public.line_movement_flags.bettors_pct IS
  'Fadereport bettors% on the side line moved TO (cross-check with OddsCrowd bets%)';
COMMENT ON COLUMN public.line_movement_flags.handle_pct IS
  'Fadereport handle% on the side line moved TO (cross-check with OddsCrowd money%)';

CREATE INDEX IF NOT EXISTS line_movement_flags_classification_idx
  ON public.line_movement_flags (sport, classification, first_seen_at DESC);

-- ─── #2 · close_locked_at on every sport's game_context ────────────────

ALTER TABLE public.mlb_game_context   ADD COLUMN IF NOT EXISTS close_locked_at TIMESTAMPTZ;
ALTER TABLE public.nfl_game_context   ADD COLUMN IF NOT EXISTS close_locked_at TIMESTAMPTZ;
ALTER TABLE public.ncaaf_game_context ADD COLUMN IF NOT EXISTS close_locked_at TIMESTAMPTZ;
ALTER TABLE public.ncaab_game_context ADD COLUMN IF NOT EXISTS close_locked_at TIMESTAMPTZ;

COMMENT ON COLUMN public.mlb_game_context.close_locked_at IS
  'Set by freeze_closing_lines.py when the true close snapshot is taken (per line_movement_config offset). Null = still polling.';

-- ─── #3 · clv_snapshots table (per-pick CLV) ──────────────────────────

CREATE TABLE IF NOT EXISTS public.clv_snapshots (
  id             BIGSERIAL PRIMARY KEY,
  sport          TEXT NOT NULL,
  game_id        TEXT NOT NULL,
  game_date      DATE NOT NULL,

  -- What we picked
  market         TEXT NOT NULL,           -- 'spread' | 'total' | 'ml'
  pick_side      TEXT NOT NULL,           -- 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
  our_number     NUMERIC NOT NULL,        -- our line/total/odds at pick time
  our_odds       INTEGER,                 -- our ML odds (american) at pick time
  our_tier       TEXT,                    -- LEAN|STRONG|PRIME (pick tier)
  our_model      TEXT,                    -- jerry|panel|v4|conf|etc — who owned the pick

  -- What the market closed at
  close_number   NUMERIC,
  close_odds     INTEGER,

  -- CLV computation
  clv            NUMERIC,                 -- positive = we beat close, negative = market went against us
  clv_direction  TEXT,                    -- 'FOR' (we beat close) | 'AGAINST' (market moved against us)

  -- Bookkeeping
  computed_at    TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (sport, game_id, market, pick_side, our_model)
);

CREATE INDEX IF NOT EXISTS clv_snapshots_sport_date_idx
  ON public.clv_snapshots (sport, game_date DESC);
CREATE INDEX IF NOT EXISTS clv_snapshots_model_tier_idx
  ON public.clv_snapshots (sport, our_model, our_tier, game_date DESC);

COMMENT ON TABLE public.clv_snapshots IS
  'Per-pick CLV. Populated by compute_clv.py after grading. clv>0 = beat close, clv<0 = market moved against.';

NOTIFY pgrst, 'reload schema';
