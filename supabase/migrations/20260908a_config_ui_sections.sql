-- 20260908a — config_ui_sections + seed defaults
--
-- Ships the render manifest table that lets the client hide/rename/reorder
-- UI sections without a rebuild. Every future UI-only decision becomes a
-- SQL edit instead of a v1.0.x binary submission.
--
-- Rationale: project_ui_toggle_infrastructure_908 memory. Team Matchup vs
-- Team Stats redundancy on NFL Game Detail (noticed 9/8 post-submit) is
-- the first example. Without this table every similar decision costs
-- 2-4 days of rebuild + Apple review.
--
-- v1.0.1 client wraps every <Section> in `if (isSectionEnabled(...))`.
-- Default row for every section is enabled=true so this is fully backwards-
-- compatible on first deploy. Then we hide the redundant Team Matchup by
-- flipping one row.

CREATE TABLE IF NOT EXISTS public.config_ui_sections (
  sport         text NOT NULL,           -- 'ALL' or sport code ('MLB','NFL','NCAAF',...)
  surface       text NOT NULL,           -- 'game_detail', 'games_tab', 'home', 'steam_room'
  section_key   text NOT NULL,           -- 'team_matchup', 'team_stats', 'money_flow', ...
  enabled       boolean NOT NULL DEFAULT true,
  order_idx     integer DEFAULT 100,     -- lower = higher on screen (v2 will consume this)
  label_override text,                   -- optional display-label override
  hint_override  text,                   -- optional hint-line override
  reason        text,                    -- freeform note about why the row exists (audit trail)
  updated_at    timestamptz DEFAULT now(),
  updated_by    text DEFAULT 'system',
  PRIMARY KEY (sport, surface, section_key)
);

-- Fast lookup for the client's per-surface fetch pattern
CREATE INDEX IF NOT EXISTS idx_config_ui_sections_surface
  ON public.config_ui_sections (surface, sport);

-- RLS: read-only for anon, so client can fetch without service key
ALTER TABLE public.config_ui_sections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS config_ui_sections_read ON public.config_ui_sections;
CREATE POLICY config_ui_sections_read
  ON public.config_ui_sections
  FOR SELECT
  TO anon, authenticated
  USING (true);

-- Only service_role writes
DROP POLICY IF EXISTS config_ui_sections_service_write ON public.config_ui_sections;
CREATE POLICY config_ui_sections_service_write
  ON public.config_ui_sections
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

-- ─── SEED: every currently-hardcoded Section starts enabled ───
-- ALL means "no sport override" — sport-specific rows override the ALL row.
-- Order matters only when v2 client consumes order_idx; for v1, purely
-- documentary.

-- ── Universal Game Detail sections (shared across sports) ──
INSERT INTO public.config_ui_sections (sport, surface, section_key, enabled, reason) VALUES
  ('ALL', 'game_detail', 'market',                  true,  'top market chip row — spread/total/ML at a glance'),
  ('ALL', 'game_detail', 'predicted_score',         true,  'model-projected score range'),
  ('ALL', 'game_detail', 'stat_projections',        true,  'per-player stat implied by models'),
  ('ALL', 'game_detail', 'money_flow',              true,  'bets vs money · sharp vs public splits'),
  ('ALL', 'game_detail', 'line_movement',           true,  'open → current line drift'),
  ('ALL', 'game_detail', 'model_consensus',         true,  'model panel agreement chart'),
  ('ALL', 'game_detail', 'external_handicappers',   true,  'per-source aggregated public picks'),
  ('ALL', 'game_detail', 'recent_schedule',         true,  'last 5 games ATS + O/U rollup'),
  ('ALL', 'game_detail', 'situational_records',     true,  'record buckets × market with hit-color'),
  ('ALL', 'game_detail', 'team_stats',              true,  'raw value + rank per stat'),
  ('ALL', 'game_detail', 'sportsbook_odds',         true,  'per-book lines + parlay/log-pick actions'),
  ('ALL', 'game_detail', 'weather',                 true,  'outdoor sports — game-time forecast'),
  ('ALL', 'game_detail', 'injuries',                true,  'Out/Doubtful/Questionable list'),

-- ── Sport-specific rows: FBS/NCAAF/NFL football ──
  ('NFL',   'game_detail', 'team_matchup',          true,  'efficiency + EPA blend (NFL flavor)'),
  ('NCAAF', 'game_detail', 'team_matchup',          true,  'season stats + rank (NCAAF flavor)'),
  ('NFL',   'game_detail', 'qb_matchup',            true,  'starters + weekly projections'),
  ('NCAAF', 'game_detail', 'qb_matchup',            true,  'starters + weekly projections'),
  ('NFL',   'game_detail', 'rosters_continuity',    true,  'returning production + physicality'),
  ('NCAAF', 'game_detail', 'rosters_continuity',    true,  'returning production + physicality'),
  ('NCAAF', 'game_detail', 'efficiency_ratings',    true,  'SP+ blend'),
  ('NCAAF', 'game_detail', 'trends_tendencies',     true,  'ATS/OU streaks + coach patterns'),

-- ── NBA-specific ──
  ('NBA', 'game_detail', 'team_snapshot',           true,  'net rating + pace + Elo'),
  ('NBA', 'game_detail', 'rest_b2b',                true,  'rest days + back-to-back flag'),
  ('NBA', 'game_detail', 'four_factors',            true,  'eFG · TOV · ORB · FT'),
  ('NBA', 'game_detail', 'pace_tempo',              true,  'projected possessions'),

-- ── NCAAB-specific ──
  ('NCAAB', 'game_detail', 'four_factors_ordered',  true,  'ordered by predictive weight'),
  ('NCAAB', 'game_detail', 'form_rest',             true,  'form + rest chip')
ON CONFLICT (sport, surface, section_key) DO NOTHING;

-- Post-migration PostgREST schema reload so client sees new table immediately
NOTIFY pgrst, 'reload schema';

-- Verification queries (safe re-run):
-- SELECT sport, section_key, enabled FROM config_ui_sections
--   WHERE surface = 'game_detail' ORDER BY sport, section_key;
--
-- Example: after v1.0.1 ships, hide NFL Team Matchup (redundant with Team Stats):
-- UPDATE config_ui_sections SET enabled = false, reason = 'redundant w/ Team Stats',
--   updated_at = now(), updated_by = 'andy'
--   WHERE sport = 'NFL' AND surface = 'game_detail' AND section_key = 'team_matchup';
