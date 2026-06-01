-- 2026-06-01 (b) — Jerry per-batter HR contribution column for HR Watch.
--
-- User direction 6/1 (post-app-fixes): "Like #3 as well" — Jerry per-batter
-- integration. Phase 1: allocator function returning expected HR contribution
-- per batter, computed against Jerry's already-existing team run projection
-- + situational multipliers (park, wind, temp, FB tilt, platoon, Statcast).
--
-- Stored in shadow mode for 7-14 days. After audit shows lift over the
-- existing score, blended into final HR Watch ranking. Until then it just
-- lives alongside score for comparison.

ALTER TABLE mlb_hr_watch
    ADD COLUMN IF NOT EXISTS jerry_hr_contribution NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_allocated_pa NUMERIC,
    ADD COLUMN IF NOT EXISTS jerry_signals JSONB;

COMMENT ON COLUMN mlb_hr_watch.jerry_hr_contribution IS
  'Expected HR contribution for this batter per Jerrys per-batter allocator.
   Computed as regressed_hr_rate * allocated_pa * product(situational multipliers:
   park, temp, wind, pitcher FB tilt, platoon, Statcast plus). Shadow mode
   2026-06-01 to 2026-06-15 — compare audit hit rate vs the legacy score
   before blending into final ranking.';

COMMENT ON COLUMN mlb_hr_watch.jerry_signals IS
  'Per-batter Jerry component breakdown {allocated_pa, base_rate, park_mult,
   wind_mult, temp_mult, fb_mult, platoon_mult, statcast_mult, raw_contribution}
   so the app + audit can show transparency on Jerrys per-batter math.';

NOTIFY pgrst, 'reload schema';
