-- 2026-07-31g · Enable sport_tab for every sport, in-season or not.
--
-- User caught this: only MLB / NBA / UFC showed in the sport picker
-- because my 20260731f seed set sport_tab=false for out-of-season
-- sports (NFL/NCAAF/NCAAB/NHL). That defeats the "no app resubmit"
-- pattern — users should see every sport year-round with an off-season
-- empty state when there are no games, not have tabs appear and
-- disappear.
--
-- New policy: sport_tab is always TRUE. Off-season UX handled by the
-- sport view's own empty state ("Season starts Aug 7 · check back then").
-- The `season_active` flag (added below) can gate per-sport banners /
-- messaging when we want to differentiate "in season with games today"
-- from "in season but off-day" from "off-season."

UPDATE feature_flags
   SET enabled = true,
       note    = COALESCE(note, '') || ' [7/31g: always-visible policy]',
       updated_at = now()
 WHERE feature = 'sport_tab'
   AND enabled = false;

-- New flag: season_active — true when the sport currently has games
-- being played this week. Used for banners / messaging, not for tab
-- visibility. Seed to match today's reality.
INSERT INTO feature_flags (sport, feature, enabled, note) VALUES
  ('MLB',   'season_active', true,  'Regular season through Sept 2026'),
  ('NBA',   'season_active', true,  'Regular season live'),
  ('UFC',   'season_active', true,  'Weekly fight cards'),
  ('NFL',   'season_active', false, 'Preseason opens Aug 7 2026'),
  ('NCAAF', 'season_active', false, 'Season opens Aug 22 2026'),
  ('NCAAB', 'season_active', false, 'Season opens Nov 2026'),
  ('NHL',   'season_active', false, 'Season opens Oct 2026')
ON CONFLICT (sport, feature) DO NOTHING;

NOTIFY pgrst, 'reload schema';
