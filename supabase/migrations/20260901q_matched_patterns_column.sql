-- matched_patterns JSONB column (2026-09-01) — Vault Match render path
--
-- Adds matched_patterns[] JSONB to every sport's game_context so the
-- attach script (mlb_pipeline/attach_vault_matches.py) can persist
-- which patterns fired on each game. App reads this column to render
-- the 🎯 Vault Match badge — no client-side pattern evaluation.
--
-- Shape of each entry (context builder writes; app reads):
--   {"key": str, "label": str, "hit_pct": num, "n": int,
--    "description": str}
--
-- Default '[]' so existing rows behave as "no patterns matched" until
-- the attach script backfills. Idempotent via IF NOT EXISTS.

ALTER TABLE public.mlb_game_context
  ADD COLUMN IF NOT EXISTS matched_patterns JSONB DEFAULT '[]'::jsonb;

ALTER TABLE public.nfl_game_context
  ADD COLUMN IF NOT EXISTS matched_patterns JSONB DEFAULT '[]'::jsonb;

ALTER TABLE public.ncaaf_game_context
  ADD COLUMN IF NOT EXISTS matched_patterns JSONB DEFAULT '[]'::jsonb;

-- NBA / NCAAB / NHL: add defensively even though pattern catalog for
-- them is empty today. Ships the column so future pattern additions
-- don't need a follow-up migration.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema='public' AND table_name='nba_game_context') THEN
    EXECUTE 'ALTER TABLE public.nba_game_context ADD COLUMN IF NOT EXISTS matched_patterns JSONB DEFAULT ''[]''::jsonb';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema='public' AND table_name='ncaab_game_context') THEN
    EXECUTE 'ALTER TABLE public.ncaab_game_context ADD COLUMN IF NOT EXISTS matched_patterns JSONB DEFAULT ''[]''::jsonb';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema='public' AND table_name='nhl_game_context') THEN
    EXECUTE 'ALTER TABLE public.nhl_game_context ADD COLUMN IF NOT EXISTS matched_patterns JSONB DEFAULT ''[]''::jsonb';
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
