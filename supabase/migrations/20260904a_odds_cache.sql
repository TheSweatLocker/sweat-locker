-- 2026-09-04 odds_cache — server-side cache for The Odds API proxy
-- ================================================================
-- Idempotent: safe to run on a fresh DB OR on top of the older
-- odds_cache schema (id/cache_key/data/fetched_at only) some environments
-- may already have. Adds any missing columns + indexes; won't fail if
-- the table already exists.
--
-- Backs supabase/functions/odds-proxy. Every proxy request first checks
-- this table; on hit (expires_at > now) returns cached data and NO
-- upstream Odds API credit is spent. Kills per-user Odds cost burn.
--
-- TTL policy (set by the edge function, not the table):
--   /historical/*   → 24h
--   /scores         → 5min
--   /odds           → 60s
--   /events         → 5min
-- ================================================================

-- 1. Base table (fresh install)
CREATE TABLE IF NOT EXISTS public.odds_cache (
    cache_key text PRIMARY KEY,
    endpoint text,
    data jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    hit_count int NOT NULL DEFAULT 0
);

-- 2. Backfill columns for older-schema installs (id/cache_key/data/fetched_at)
ALTER TABLE public.odds_cache
    ADD COLUMN IF NOT EXISTS endpoint text,
    ADD COLUMN IF NOT EXISTS expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS hit_count int NOT NULL DEFAULT 0;

-- 3. Make cache_key uniquely indexed so upserts work (if it wasn't PK)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname='public' AND tablename='odds_cache' AND indexname='odds_cache_cache_key_key'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='odds_cache_pkey' AND conrelid='public.odds_cache'::regclass
        AND (
            SELECT attname FROM pg_attribute
            WHERE attrelid='public.odds_cache'::regclass AND attnum=ANY(conkey) LIMIT 1
        ) = 'cache_key'
    ) THEN
        BEGIN
            ALTER TABLE public.odds_cache ADD CONSTRAINT odds_cache_cache_key_key UNIQUE (cache_key);
        EXCEPTION WHEN duplicate_table OR duplicate_object THEN
            NULL;
        END;
    END IF;
END $$;

-- 4. Indexes (safe on re-run)
CREATE INDEX IF NOT EXISTS idx_odds_cache_expires
    ON public.odds_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_odds_cache_endpoint
    ON public.odds_cache(endpoint);

-- 5. Backfill expires_at on any pre-existing rows (assume expired so
-- they get refreshed on next proxy call — safe default).
UPDATE public.odds_cache
   SET expires_at = COALESCE(expires_at, fetched_at)
 WHERE expires_at IS NULL;

COMMENT ON TABLE public.odds_cache IS
    'Server-side cache for The Odds API proxy (supabase/functions/odds-proxy). '
    'cache_key = endpoint + normalized params. Rows survive briefly (60s-24h) '
    'per edge function TTL logic. Kills per-user Odds credit burn.';

NOTIFY pgrst, 'reload schema';
