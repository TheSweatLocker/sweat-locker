-- 2026-09-17d — home_banners table for server-controlled Home tab notes.
-- ==================================================================
-- Andy 9/17 PM: "I feel like we have drifted from minimal hardcoding
-- so i am not having to submit version to app store for little things
-- like this. I want server side for all these notes and specific
-- notes for sports as needed."
--
-- Prior state:
--   * admin_notice — urgent operational/status notes at the top of
--     every screen (info/warning/critical severity). Already server-
--     driven. Renders via AdminNoticeBanner.
--   * HomeStreakBanner — auto-computed hot-streak banners on Home
--     tab (Prime Props L7D, Sharp 3d hot, Ledger green, etc). Copy +
--     thresholds HARDCODED in client. Every template tweak or new
--     banner class needed an App Store rebuild.
--
-- Ship: split hot-streak / product-marketing banners out to a
-- server-controlled table so:
--   * copy is edited via SQL (INSERT / UPDATE), not client code
--   * new banner types are added without a client rebuild
--   * per-sport filtering works out of the box (sport column)
--   * per-route filtering works (route column — e.g. only show on
--     Home, only on Games:NFL, etc.)
--   * auto-computed banners come from a cron that INSERTs with
--     origin='auto', ad-hoc admin pushes use origin='admin'
--   * priority sorts multiple candidates; client rotates through
--     the top-N every 8s (same UX as HomeStreakBanner today)
--
-- The auto-cron is a follow-up (script mlb_pipeline/compute_home_
-- banners.py, runs post-pipeline). Meanwhile Andy can INSERT rows
-- manually to test + brief anything hot.
-- ==================================================================

CREATE TABLE IF NOT EXISTS public.home_banners (
    id BIGSERIAL PRIMARY KEY,

    -- Display fields
    icon        TEXT NOT NULL DEFAULT '🔥',
    message     TEXT NOT NULL,
    -- Optional deep link — 'sharp' opens Steam Room→Sharp, 'ladder'
    -- opens Steam Room→Ladder, 'ledger' opens Steam Room→Ledger,
    -- 'jerry' opens Prop Jerry tab, 'daily_degen' opens Daily Degen.
    -- Null → non-tappable banner.
    deep_link   TEXT,

    -- Scoping
    -- sport: null = show to all viewers; 'MLB'/'NFL'/'NCAAF'/etc =
    -- only show when currentSport matches (client passes gamesSport).
    sport       TEXT,
    -- route: null = show on all screens; 'home' = only on Home tab.
    -- 'games'/'jerry'/'steam'/'mybets' scope to those tabs.
    route       TEXT,

    -- Ordering + display gate
    -- priority: higher = more prominent. When multiple banners are
    -- eligible, top-3 by priority render in the rotation.
    priority    INTEGER NOT NULL DEFAULT 50,

    -- Time window — banner shows when NOW is between starts_at and
    -- expires_at. Both nullable: null starts_at = "as soon as visible",
    -- null expires_at = "until manually cleared or overwritten by cron."
    starts_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,

    -- Provenance
    -- origin: 'auto' = cron-inserted (compute_home_banners.py), 'admin' =
    -- human-inserted via SQL/console. Auto rows can be safely replaced
    -- by newer cron runs (dedupe on kind). Admin rows persist until
    -- their expires_at fires or someone deletes them.
    origin      TEXT NOT NULL DEFAULT 'admin',
    -- kind: stable identifier used by the cron for dedup on re-run —
    -- 'prime_props_l7d_MLB', 'sharp_3d_hot_ALL', 'daily_degen_streak'.
    -- Client doesn't read this; it's for cron upsert semantics.
    kind        TEXT,

    -- Dismissibility (per-viewer AsyncStorage — see AdminNoticeBanner)
    dismissible BOOLEAN NOT NULL DEFAULT FALSE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hot-path index: the client query filters expires_at + sport + route
-- and sorts by priority. Full index (no WHERE predicate) — NOW() is not
-- IMMUTABLE so Postgres refuses it in an index predicate (2026-09-17
-- migration failure recovery). Full index is cheap since the table
-- stays small (dozens of rows, not millions).
CREATE INDEX IF NOT EXISTS home_banners_active_idx
  ON public.home_banners (expires_at, priority DESC, starts_at DESC);

-- Cron upsert convenience: unique on (kind, origin) so cron can
-- INSERT ... ON CONFLICT (kind, origin) DO UPDATE and never create
-- dupe auto-banners across runs. Admin rows have kind=NULL so they
-- don't collide with cron rows.
CREATE UNIQUE INDEX IF NOT EXISTS home_banners_kind_origin_uniq
  ON public.home_banners (kind, origin)
  WHERE kind IS NOT NULL;

-- RLS: readable by anon + authenticated. Writable only by service role
-- (cron script + admin console). No user writes.
ALTER TABLE public.home_banners ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS home_banners_read_all ON public.home_banners;
CREATE POLICY home_banners_read_all ON public.home_banners
  FOR SELECT
  TO anon, authenticated
  USING (TRUE);

-- Seed one row so client can test the render immediately (matches
-- today's real MLB Prime Props L7D data). Cron will replace on
-- next run.
INSERT INTO public.home_banners (icon, message, deep_link, sport, route, priority, expires_at, origin, kind)
VALUES (
    '🎯',
    'MLB Prime Props L7D: 222-46 (83%), +123.1u',
    'jerry',
    NULL,       -- show for all sports
    'home',     -- Home tab only
    100,
    NOW() + INTERVAL '2 hours',   -- cron should overwrite well before this
    'auto',
    'prime_props_l7d_MLB'
)
ON CONFLICT (kind, origin) DO UPDATE
  SET message = EXCLUDED.message,
      priority = EXCLUDED.priority,
      expires_at = EXCLUDED.expires_at,
      created_at = NOW();

NOTIFY pgrst, 'reload schema';
