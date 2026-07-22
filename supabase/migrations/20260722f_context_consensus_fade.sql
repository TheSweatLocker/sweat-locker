-- 2026-07-22 — Consensus-fade alert fields on mlb_game_context.
--
-- Detector writes into these when today's external picks show
-- consensus >= 75% + audit-fade-tagged sources involved. App reads
-- flag to render the Tier-1 "Consensus Fade" chip on the card.
--
-- Also stores the raw consensus % and dominant side so app can display
-- context ("7 of 9 books on LAD - historical fade signal").

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS consensus_fade_flag       BOOLEAN,
  ADD COLUMN IF NOT EXISTS consensus_fade_side       TEXT,     -- 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
  ADD COLUMN IF NOT EXISTS consensus_fade_pct        NUMERIC,  -- 0.0-1.0
  ADD COLUMN IF NOT EXISTS consensus_fade_n          INT,      -- source count
  ADD COLUMN IF NOT EXISTS consensus_fade_note       TEXT;

CREATE INDEX IF NOT EXISTS idx_mlb_game_context_fade_flag
  ON mlb_game_context (consensus_fade_flag)
  WHERE consensus_fade_flag = TRUE;

NOTIFY pgrst, 'reload schema';
