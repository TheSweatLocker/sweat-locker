-- 2026-09-09: player_game_log — universal per-game history for prop graphs.
--
-- Root problem: app/index.tsx:7369 fetchPropHistory hardcoded to
-- balldontlie.io (NBA only). MLB/NFL/NHL/NCAAF/NCAAB props all showed
-- "No game log available" because the API didn't know the players.
--
-- Fix: server-side precompute per-game history into this table. App
-- reads directly from Supabase, sport-agnostic. Backfilled by
-- per-sport scripts (backfill_player_game_log_mlb.py etc.) on daily cron.
--
-- Schema is intentionally flexible:
--   - `stats` jsonb holds all per-sport counts (Ks, hits, outs, pts, reb, ...)
--   - `result_value` is a single numeric — the "bar height" for the graph
--   - `prop_type` optional pointer to which stat this row is graphing
--     (e.g. 'ks' → result_value = strikeouts; 'hits' → hits)
--
-- App query: WHERE sport = ? AND player_key = ? AND prop_type = ?
--            ORDER BY game_date DESC LIMIT 10

CREATE TABLE IF NOT EXISTS public.player_game_log (
  id            BIGSERIAL PRIMARY KEY,
  sport         TEXT NOT NULL,          -- 'MLB' | 'NFL' | 'NBA' | 'NHL' | 'NCAAF' | 'NCAAB'
  player_id     TEXT,                   -- per-sport upstream id (e.g. MLB Stats API pitcherId)
  player_key    TEXT NOT NULL,          -- normalized name (used by app for lookup)
  player_name   TEXT NOT NULL,          -- display name
  game_date     DATE NOT NULL,
  game_id       TEXT,                   -- per-sport game id when available
  opponent      TEXT,                   -- opposing team display
  home_away     TEXT,                   -- 'H' | 'A'
  prop_type     TEXT,                   -- 'ks' | 'er' | 'bb' | 'ha' | 'outs' | 'hits' | 'tb' | 'pts' | 'reb' | 'ast' | ...
  result_value  NUMERIC,                -- the number that draws the bar
  line          NUMERIC,                -- the line the player faced that day (if known)
  hit           BOOLEAN,                -- did they beat the line (if line known)
  stats         JSONB,                  -- full sport-specific stat bag for that game
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (sport, player_key, game_date, prop_type)
);

CREATE INDEX IF NOT EXISTS player_game_log_lookup
  ON public.player_game_log (sport, player_key, prop_type, game_date DESC);

CREATE INDEX IF NOT EXISTS player_game_log_recent
  ON public.player_game_log (sport, game_date DESC);

-- App reads via anon key (public per-game history is public info).
ALTER TABLE public.player_game_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS player_game_log_public_read ON public.player_game_log;
CREATE POLICY player_game_log_public_read ON public.player_game_log
  FOR SELECT TO anon, authenticated
  USING (true);

GRANT SELECT ON public.player_game_log TO anon, authenticated;
GRANT ALL ON public.player_game_log TO service_role;
GRANT USAGE, SELECT ON SEQUENCE player_game_log_id_seq TO service_role;

NOTIFY pgrst, 'reload schema';
