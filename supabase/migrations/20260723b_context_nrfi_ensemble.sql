-- 2026-07-23 — NRFI ensemble fields on mlb_game_context.
--
-- Combines rich MC's native p_nrfi + sklearn nrfi_score into a
-- unified ensemble tier. Backtest baseline (n=241, both agree):
-- 61.8% hit rate — a real actionable NRFI lane distinct from either
-- model alone.
--
-- Populated by enrich_monte_carlo._compute_nrfi_ensemble per game.
--
-- Tier ladder:
--   ELITE  — both models agree + high conf
--   STRONG — MC 70%+ alone OR sklearn 80%+ alone
--   LEAN   — both agree at moderate conf
--   SKIP   — models disagree at moderate conf

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS nrfi_ensemble_tier    TEXT,      -- ELITE / STRONG / LEAN / SKIP
  ADD COLUMN IF NOT EXISTS nrfi_ensemble_pick    TEXT,      -- 'NRFI' / 'YRFI' / null
  ADD COLUMN IF NOT EXISTS nrfi_ensemble_conf    NUMERIC,   -- 0.5-1.0
  ADD COLUMN IF NOT EXISTS nrfi_ensemble_reason  TEXT;      -- human-readable

CREATE INDEX IF NOT EXISTS idx_mlb_game_context_nrfi_ens_tier
  ON mlb_game_context (nrfi_ensemble_tier)
  WHERE nrfi_ensemble_tier IN ('ELITE', 'STRONG');

NOTIFY pgrst, 'reload schema';
