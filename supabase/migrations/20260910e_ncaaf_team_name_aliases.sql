-- 2026-09-10 · NCAAF team name aliases table
--
-- Problem: ncaaf_game_context stores team names from the odds/schedule pipe
-- ("Appalachian State", "Hawaii", "San Jose State", "Virginia Tech Hokies")
-- while ncaaf_team_stats stores CFBD canonical names ("App State", "Hawai'i",
-- "San José State", "Virginia Tech"). Direct string join drops SP+/EPA for
-- these teams — 43 teams missing SP+ on the current-week card as a result.
--
-- Solution: alias table maps ctx-side team names to their CFBD canonical.
-- ncaaf_game_context builder consults this resolver before the stats join
-- so name-variant mismatches never drop SP+ silently.
--
-- FCS teams (Cal Poly, Sacred Heart, etc.) have no CFBD SP+ regardless
-- of name — they're handled by the FCS-tagging fallback in the builder,
-- not this table. This table is FBS-name-variants only.

CREATE TABLE IF NOT EXISTS ncaaf_team_name_aliases (
  ctx_name       TEXT PRIMARY KEY,   -- how ncaaf_game_context stores it
  cfbd_canonical TEXT NOT NULL,      -- how CFBD /ratings/sp returns it
  notes          TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the known 2026 mismatches. Add rows here as new mismatches surface
-- (schedule an audit script to detect + open a PR with the diffs).
INSERT INTO ncaaf_team_name_aliases (ctx_name, cfbd_canonical, notes) VALUES
  ('Appalachian State',      'App State',                'CFBD uses short form'),
  ('Hawaii',                 'Hawai''i',                 'CFBD uses macron/apostrophe'),
  ('San Jose State',         'San José State',           'CFBD uses accented é'),
  ('Virginia Tech Hokies',   'Virginia Tech',            'Odds pipe includes mascot; strip it'),
  ('Louisiana Monroe',       'Louisiana Monroe',         'Verify: may need ULM'),
  ('Southern',               'Southern',                 'FCS — no SP+ expected')
ON CONFLICT (ctx_name) DO UPDATE
  SET cfbd_canonical = EXCLUDED.cfbd_canonical,
      notes          = EXCLUDED.notes,
      updated_at     = NOW();

NOTIFY pgrst, 'reload schema';
