-- 2026-09-04 odds_cache — server-side cache for The Odds API proxy
-- ================================================================
-- Backs supabase/functions/odds-proxy. Every proxy request first
-- checks this table; on hit (expires_at > now) returns cached data
-- and NO upstream credit is spent. Kills per-user Odds cost burn.
--
-- TTL policy (set by the edge function, not the table):
--   /historical/*   → 24h
--   /scores         → 5min
--   /odds           → 60s
--   /events         → 5min
--
-- ================================================================

CREATE TABLE IF NOT EXISTS public.odds_cache (
    cache_key text PRIMARY KEY,          -- endpoint + sorted params
    endpoint text NOT NULL,              -- for reporting / TTL audit
    data jsonb NOT NULL,                 -- upstream response body
    fetched_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,     -- edge function sets per-endpoint TTL
    hit_count int NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_odds_cache_expires
    ON public.odds_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_odds_cache_endpoint
    ON public.odds_cache(endpoint);

-- Auto-cleanup: rows older than 48h can be dropped. Cron will handle
-- this via a scheduled DELETE (or just let them accumulate — tiny cost).
COMMENT ON TABLE public.odds_cache IS
    'Server-side cache for The Odds API proxy (supabase/functions/odds-proxy). '
    'Cache_key = endpoint + normalized params. Rows survive briefly (60s-24h) '
    'per edge function TTL logic. Kills per-user Odds credit burn.';

NOTIFY pgrst, 'reload schema';
