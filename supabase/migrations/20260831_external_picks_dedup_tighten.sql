-- ============================================================
-- 2026-08-31: Tighten external_picks dedup key.
-- ============================================================
-- Prior key: (source, game_id, surface, pick_side, game_date)
-- Problem: When the same source's OWN pick direction flips between
-- morning and afternoon pulls (line movement causes money/bets split
-- to swap majority side), BOTH rows persist. Surfaced 8/31 on the
-- Astros/CWS game where OddsCrowd + ScoresAndOdds both had rows for
-- HOME and AWAY on the ML market. App rendered same source on both
-- sides — misleading UX.
--
-- New key: (source, game_id, surface, game_date). Drops pick_side.
-- Latest pull's direction overwrites earlier. Consensus/fade math
-- reads the *current* direction only, matching what handicappers
-- actually recommend right now.
--
-- Safe because: we already store pull_id + pulled_at on each row, so
-- historical audits of "what did source X say at 6am vs 5pm" can be
-- reconstructed from the external_picks_pull_log if ever needed.
-- ============================================================

-- Drop old constraint
ALTER TABLE external_picks
    DROP CONSTRAINT IF EXISTS external_picks_dedup_key;

-- One-time cleanup: for each (source, game_id, surface, game_date),
-- keep the MOST RECENT row (by pulled_at) and delete older direction
-- flips. Runs before the new constraint so it doesn't 23505 on legacy
-- dupes.
DELETE FROM external_picks a
USING external_picks b
WHERE a.source = b.source
  AND a.game_id = b.game_id
  AND a.surface = b.surface
  AND a.game_date = b.game_date
  AND a.pulled_at < b.pulled_at;

-- Add tightened constraint
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'external_picks_dedup_key_v2'
  ) THEN
    ALTER TABLE external_picks
      ADD CONSTRAINT external_picks_dedup_key_v2
      UNIQUE (source, game_id, surface, game_date);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
