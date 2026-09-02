-- Add `direction` column to sport_pattern_registry (2026-09-01)
--
-- Two badge types per user directive: BACK (🎯 Vault Match — pattern
-- historically hits, back the side) and FADE (⚠️ Vault Fade — pattern
-- historically loses, bet the opposite). The direction is a property
-- of the PATTERN DEFINITION (whether the matches_fn identifies a spot
-- to back or fade), not a property of the computed hit rate.
--
-- Nightly recompute writes direction from PATTERN_CATALOG entry. App
-- render branches on it: BACK → 🎯 accent chip; FADE → ⚠️ warn chip.
--
-- Default 'BACK' for existing rows (all 3 starter patterns are BACK).

ALTER TABLE public.sport_pattern_registry
  ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'BACK'
    CHECK (direction IN ('BACK', 'FADE'));

NOTIFY pgrst, 'reload schema';
