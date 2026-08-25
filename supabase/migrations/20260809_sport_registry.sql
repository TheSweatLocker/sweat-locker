-- Sport registry (2026-08-09).
--
-- Backend-controls the universe of sports the app knows about + display
-- metadata (emoji, display order). Removes the hardcoded `SPORTS` array
-- and `SPORT_EMOJI` dict from app/index.tsx.
--
-- Feature-flag gating (per-tab activation) stays in `feature_flags` table —
-- this table is the CATALOG (what sports exist + how to display), flags
-- are the PERMISSIONS (which tabs render the sport). Together:
--   sport_registry.active=false  → sport hidden everywhere (mothballed)
--   sport_registry.active=true   → sport in universe
--   feature_flags.enabled=true   → sport surfaces in specific tab
--
-- Sport codes match existing app conventions (MLB/NFL/NCAAF/NCAAB/NBA/NHL/UFC).
-- App caches on load; refreshes hourly. Adding an 8th sport = INSERT row,
-- then update feature_flags for whichever tabs should surface it.

CREATE TABLE IF NOT EXISTS public.sport_registry (
  sport TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  emoji TEXT NOT NULL,
  display_order INT NOT NULL DEFAULT 100,
  active BOOLEAN NOT NULL DEFAULT true,
  season_start DATE,           -- optional: informational
  season_end DATE,             -- optional: informational
  notes TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed with current 7 sports. Order matches current SPORTS[] const so the
-- initial deploy is a visual no-op. Emojis match current SPORT_EMOJI dict.
INSERT INTO public.sport_registry (sport, display_name, emoji, display_order, active, season_start, season_end) VALUES
  ('NBA',   'NBA',   '🏀', 10, true, '2026-10-21', '2027-06-15'),
  ('NFL',   'NFL',   '🏈', 20, true, '2026-09-04', '2027-02-15'),
  ('NHL',   'NHL',   '🏒', 30, true, '2026-10-08', '2027-06-30'),
  ('MLB',   'MLB',   '⚾', 40, true, '2026-03-27', '2026-11-05'),
  ('NCAAB', 'NCAAB', '🏀', 50, true, '2026-11-03', '2027-04-05'),
  ('NCAAF', 'NCAAF', '🏈', 60, true, '2026-08-22', '2027-01-15'),
  ('UFC',   'UFC',   '🥊', 70, true, NULL, NULL)
ON CONFLICT (sport) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  emoji = EXCLUDED.emoji,
  display_order = EXCLUDED.display_order,
  active = EXCLUDED.active,
  season_start = EXCLUDED.season_start,
  season_end = EXCLUDED.season_end,
  updated_at = NOW();

-- RLS: enable + read-only for anon (matches other pipeline-output tables)
ALTER TABLE public.sport_registry ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.sport_registry;
CREATE POLICY public_read ON public.sport_registry
  FOR SELECT TO anon, authenticated USING (true);

-- Write policy for pipeline (matches 20260717_rls_pipeline_write_hotfix pattern).
-- Will tighten to service_role only when the RLS launch-blocker fix ships.
DROP POLICY IF EXISTS public_write ON public.sport_registry;
CREATE POLICY public_write ON public.sport_registry
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS sport_registry_order_idx
  ON public.sport_registry (display_order) WHERE active = true;

NOTIFY pgrst, 'reload schema';
