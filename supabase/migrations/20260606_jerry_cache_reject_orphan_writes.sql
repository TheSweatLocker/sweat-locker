-- jerry_cache: drop client-side fallback poison writes at the database layer.
--
-- Background
-- ----------
-- The current iOS app (TestFlight build pre-2026-06-06) has a client-side
-- Claude fallback that writes to jerry_cache when the server-side game read
-- isn't found. Those writes have a broken schema:
--   - cache_key IS NULL  (server writes always set cache_key)
--   - data IS NULL       (server writes always include the struct)
--   - game_id matches  '<hash>_YYYY-MM-DD'  (not the server's `game_read_*` prefix)
--
-- The app then reads these poison rows preferentially over the canonical
-- server reads (different lookup path), so the user sees a stale narrative
-- with hallucinated pitcher names AND the Numbers panel disappears (because
-- data IS NULL means there's no struct to render).
--
-- The client code is patched in main but won't ship until the next TestFlight
-- build. In the meantime users keep generating these rows whenever they open
-- a game detail in the live app.
--
-- This migration adds a BEFORE INSERT trigger that silently drops rows
-- matching the orphan pattern. The client `.upsert()` call still succeeds
-- from the app's perspective (no error surfaced) — the row just never lands.
-- Server-side scripts (generate_mlb_game_reads.py etc.) always set
-- cache_key + data so they pass through unaffected.
--
-- Safe to keep this trigger indefinitely as a defense-in-depth measure —
-- there's no legitimate reason to write a jerry_cache row with both
-- cache_key NULL and data NULL.

CREATE OR REPLACE FUNCTION jerry_cache_reject_orphan_writes()
RETURNS TRIGGER AS $$
BEGIN
    -- Reject the orphan pattern: no cache_key AND no data.
    -- These come from the client-side Claude fallback and poison
    -- downstream reads for 8 hours.
    IF NEW.cache_key IS NULL AND NEW.data IS NULL THEN
        -- Return NULL skips the actual insert/update without raising an
        -- error. The calling client sees a 201 Created response (PostgREST
        -- treats no-op as success) but the row never persists.
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to both INSERT and UPDATE so an existing valid row can't be
-- mutated into the orphan pattern either.
DROP TRIGGER IF EXISTS reject_orphan_jerry_cache_writes ON jerry_cache;
CREATE TRIGGER reject_orphan_jerry_cache_writes
    BEFORE INSERT OR UPDATE ON jerry_cache
    FOR EACH ROW
    EXECUTE FUNCTION jerry_cache_reject_orphan_writes();

-- Reload schema cache so PostgREST sees the trigger immediately.
NOTIFY pgrst, 'reload schema';
