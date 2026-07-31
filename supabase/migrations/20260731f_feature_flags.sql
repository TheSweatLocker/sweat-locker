-- 2026-07-31f · Feature flags — the "no app resubmit" backbone.
--
-- Every sport tab, every experimental feature, gated by a row here.
-- App reads on launch + on foreground refresh. Server flips flags →
-- users see change on next open. Zero code push required.
--
-- Rows shape:
--   sport TEXT ('MLB'|'NBA'|'NFL'|'NCAAF'|'NCAAB'|'NHL'|'UFC'|'ALL')
--   feature TEXT ('sport_tab' | 'jerry_synthesis' | 'prop_synthesis' |
--                 'auto_resolver' | 'dawg' | 'daily_degen' | etc.)
--   enabled BOOL
--   min_app_version TEXT (optional gate — hide from clients below version)
--   note TEXT (why it's on/off)
--
-- Client policy:
--   * enabled=true AND client_version >= min_app_version → show
--   * enabled=false → hide (regardless of client version)
--   * feature row missing → default hide (fail-safe: launch flow must
--     explicitly enable each sport)

CREATE TABLE IF NOT EXISTS feature_flags (
    id                bigserial   PRIMARY KEY,
    sport             text        NOT NULL,
    feature           text        NOT NULL,
    enabled           boolean     NOT NULL DEFAULT false,
    min_app_version   text,
    note              text,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sport, feature)
);
CREATE INDEX IF NOT EXISTS idx_feature_flags_lookup ON feature_flags (sport, feature) WHERE enabled;

ALTER TABLE feature_flags DISABLE ROW LEVEL SECURITY;

-- Seed the current live/upcoming state so the app has something to read.
-- These match today's product surface. Server can flip any of them later
-- without touching the app.

INSERT INTO feature_flags (sport, feature, enabled, note) VALUES
  ('MLB',   'sport_tab',          true,  'Season in progress — always on'),
  ('MLB',   'jerry_synthesis',    true,  'Live 2026-07-31, jerry_reads populates 8am+2pm ET'),
  ('MLB',   'prop_synthesis',     true,  'Live 2026-07-31, prop_jerry_reads populates 2pm ET'),
  ('MLB',   'auto_resolver',      true,  'Sport-universal betResolver hits mlb_game_results'),
  ('MLB',   'dawg',               true,  'Legacy Dawg of the Day generation'),
  ('MLB',   'daily_degen',        true,  'Jerry-anchored + legacy fallback'),

  ('NBA',   'sport_tab',          true,  'Season live; leans-only mode until Nov 2026'),
  ('NBA',   'jerry_synthesis',    false, 'Waiting on NBA-specific prompt + context adapter'),
  ('NBA',   'prop_synthesis',     false, 'Waiting on nba_pipeline_props table'),
  ('NBA',   'auto_resolver',      true,  'nba_game_results table populated — resolver works'),

  ('NFL',   'sport_tab',          false, 'Enable at Aug 7 preseason'),
  ('NFL',   'jerry_synthesis',    false, 'Enable when nfl_game_context populates + prompt seeded'),
  ('NFL',   'auto_resolver',      true,  'nfl_game_results table populated'),

  ('NCAAF', 'sport_tab',          false, 'Enable at Aug 22 season'),
  ('NCAAF', 'jerry_synthesis',    false, 'Enable when ncaaf_game_context populates'),
  ('NCAAF', 'auto_resolver',      true,  'ncaaf_game_results table populated'),

  ('NCAAB', 'sport_tab',          false, 'Enable at Nov 2026 season'),
  ('NCAAB', 'jerry_synthesis',    false, 'Enable when NCAAB pipeline lands'),
  ('NCAAB', 'auto_resolver',      true,  'ncaab_game_results table populated'),

  ('NHL',   'sport_tab',          false, 'Enable when NHL pipeline scaffold ships (couple days per user)'),
  ('NHL',   'auto_resolver',      false, 'nhl_game_results table not yet created'),

  ('UFC',   'sport_tab',          true,  'Live per project_ufc_sprint_729'),
  ('UFC',   'jerry_synthesis',    false, 'Waiting on fighter-shape adapter'),
  ('UFC',   'auto_resolver',      true,  'ufc_fight_results populated — ML variant supported')
ON CONFLICT (sport, feature) DO NOTHING;

NOTIFY pgrst, 'reload schema';
