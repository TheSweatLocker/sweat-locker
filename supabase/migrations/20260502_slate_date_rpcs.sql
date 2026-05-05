-- Server-side slate-date RPCs
-- ============================================================
-- Closes the recurring UTC-vs-ET timezone bug class. Client used to derive
-- "today" via new Date().toISOString().split('T')[0] which returns UTC date,
-- so after 8pm ET the app queried for tomorrow's slate and saw nothing.
-- We've patched this 6 times now (POTD, Dawg, props, hits dedup, schedule
-- lookup, etc). Proper fix: never let the client derive a slate date.
--
-- Apply via Supabase SQL editor:
--   1. Open Supabase dashboard → SQL Editor
--   2. Paste this entire file
--   3. Run
-- After running, app should call supabase.rpc('get_todays_*') instead of
-- deriving dates and querying tables directly.

-- Helper: return today's date in America/New_York timezone (YYYY-MM-DD).
-- Single source of truth for "what is today's slate date" across all RPCs.
CREATE OR REPLACE FUNCTION slate_date_et()
RETURNS DATE
LANGUAGE SQL
STABLE
AS $$
  SELECT (NOW() AT TIME ZONE 'America/New_York')::DATE;
$$;

-- Returns today's Dawg of the Day row (or NULL if none picked).
CREATE OR REPLACE FUNCTION get_todays_dawg()
RETURNS SETOF daily_dawg
LANGUAGE SQL
STABLE
AS $$
  SELECT * FROM daily_dawg WHERE game_date = slate_date_et() LIMIT 1;
$$;

-- Returns today's POTD entry from jerry_cache.
CREATE OR REPLACE FUNCTION get_todays_potd()
RETURNS SETOF jerry_cache
LANGUAGE SQL
STABLE
AS $$
  SELECT * FROM jerry_cache
  WHERE game_id = 'best_bet_' || slate_date_et()::TEXT
  LIMIT 1;
$$;

-- Returns all of today's pipeline props.
CREATE OR REPLACE FUNCTION get_todays_pipeline_props()
RETURNS SETOF mlb_pipeline_props
LANGUAGE SQL
STABLE
AS $$
  SELECT * FROM mlb_pipeline_props
  WHERE game_date = slate_date_et()
  ORDER BY conviction DESC;
$$;

-- Returns all of today's HR Watch candidates.
CREATE OR REPLACE FUNCTION get_todays_hr_watch()
RETURNS SETOF mlb_hr_watch
LANGUAGE SQL
STABLE
AS $$
  SELECT * FROM mlb_hr_watch
  WHERE game_date = slate_date_et()
  ORDER BY score DESC;
$$;

-- Returns today's Daily Degen entry.
-- Note: daily_degen schema may differ; adjust SELECT cols if needed.
CREATE OR REPLACE FUNCTION get_todays_daily_degen()
RETURNS SETOF daily_degen
LANGUAGE SQL
STABLE
AS $$
  SELECT * FROM daily_degen WHERE game_date = slate_date_et() LIMIT 1;
$$;

-- Convenience: bare slate date string (for any client that needs it).
-- Prefer the typed RPCs above over building queries with this string —
-- this is fallback only.
CREATE OR REPLACE FUNCTION get_slate_date_et()
RETURNS TEXT
LANGUAGE SQL
STABLE
AS $$
  SELECT slate_date_et()::TEXT;
$$;

-- Permissions: allow anon role (used by Supabase JS client) to call.
-- These are read-only RPCs over already-public tables, so anon access is fine.
GRANT EXECUTE ON FUNCTION slate_date_et()           TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_todays_dawg()             TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_todays_potd()             TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_todays_pipeline_props()   TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_todays_hr_watch()         TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_todays_daily_degen()      TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_slate_date_et()           TO anon, authenticated;
