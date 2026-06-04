-- 2026-06-04 — primary_play staleness tracking.
--
-- Bug: CLE @ NYY 6/4 showed "Over 3.5 vs market 7" in the sweat card. The
-- primary_play JSONB was stored with a stale line (3.5 — likely the F5
-- estimated line or an earlier-day misread) and never refreshed when the
-- actual market closed at 7-8.5. App rendered the stale recommendation.
--
-- Fix: add primary_play_computed_at TIMESTAMPTZ. Stamped on every
-- compute_primary_play write. App reads this and suppresses the
-- primary_play render if it's older than PRIMARY_PLAY_STALE_HOURS (4).
-- Mirrors the sweat_tier_locked_at architecture (6/3).

ALTER TABLE mlb_game_context
    ADD COLUMN IF NOT EXISTS primary_play_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN mlb_game_context.primary_play_computed_at IS
  'When primary_play was last written by compute_primary_play. App-side stale-check suppresses rendering when older than 4 hours. Prevents the "Over 3.5 vs market 7" UX bug where stale plays leak past cron failures.';

NOTIFY pgrst, 'reload schema';
