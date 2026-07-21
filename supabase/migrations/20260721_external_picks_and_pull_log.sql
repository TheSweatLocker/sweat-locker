-- 2026-07-21: External picks aggregation + pull log
-- ============================================================
-- Two tables that power the "External Sources" tab feature:
--
--   external_pull_log  — one row per scheduled/manual pull attempt
--                        (tracks success/failure, count, timing, provenance)
--   external_picks     — one row per attributed pick, foreign-keyed to
--                        the pull that produced it
--
-- Design goals:
-- 1. Full provenance: every pick traceable to a pull_id → scheduled_at
--    → agent version → source URL
-- 2. Debug-friendly: failed pulls persist so we can see WHICH source
--    failed at WHICH time
-- 3. Rollback-friendly: bad pull → delete all picks with that pull_id
-- 4. User-facing transparency: "62 picks pulled 12:14 PM across 14 games"
--    is queryable off pull_log
--
-- Cadence per sport (per project_external_aggregation_launch memory):
--   MLB   noon + 5PM ET daily
--   NFL   Thu 4PM + Sat 10AM
--   NCAAF Wed 6PM + Fri 4PM
--   NCAAB Weeknight 5:30PM + Sat 10AM/4PM + Sun 11AM
--   NBA   4PM daily (season)
--   NHL   3PM daily (season)
--   UFC   Sat noon
--
-- Idempotent (CREATE TABLE IF NOT EXISTS everywhere).

-- ────────────────────────────────────────────────────────────
-- external_pull_log — the audit trail
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.external_pull_log (
    id              bigserial PRIMARY KEY,
    pull_id         uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    sport           text NOT NULL,
    source          text NOT NULL,
    scheduled_at    timestamptz NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    status          text NOT NULL DEFAULT 'running',
    picks_pulled    int DEFAULT 0,
    games_covered   int DEFAULT 0,
    error_message   text,
    source_url      text,
    http_status     int,
    duration_ms     int,
    triggered_by    text NOT NULL,
    agent_version   text,
    notes           text,
    CONSTRAINT ext_pull_status_ck CHECK (status IN ('running','success','failed','partial'))
);

CREATE INDEX IF NOT EXISTS idx_ext_pull_log_sport_date
    ON public.external_pull_log (sport, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_ext_pull_log_source_started
    ON public.external_pull_log (source, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ext_pull_log_status
    ON public.external_pull_log (status);

-- ────────────────────────────────────────────────────────────
-- external_picks — the attributed picks feed
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.external_picks (
    id              bigserial PRIMARY KEY,
    pull_id         uuid REFERENCES public.external_pull_log(pull_id) ON DELETE CASCADE,
    game_id         text NOT NULL,
    sport           text NOT NULL,
    game_date       date NOT NULL,
    source          text NOT NULL,
    surface         text NOT NULL,
    pick_side       text,
    pick_line       numeric,
    odds_american   int,
    confidence      text,
    raw_text        text,
    source_url      text,
    pulled_at       timestamptz NOT NULL DEFAULT now(),
    ttl_hours       int DEFAULT 12,
    fade_flag       text,
    CONSTRAINT ext_picks_surface_ck CHECK (surface IN ('ml','total','rl','prop','sharp_signal','other')),
    CONSTRAINT ext_picks_fade_ck CHECK (fade_flag IS NULL OR fade_flag IN ('boost','trust','neutral','fade'))
);

CREATE INDEX IF NOT EXISTS idx_ext_picks_game
    ON public.external_picks (game_id, sport);
CREATE INDEX IF NOT EXISTS idx_ext_picks_sport_date
    ON public.external_picks (sport, game_date);
CREATE INDEX IF NOT EXISTS idx_ext_picks_source_date
    ON public.external_picks (source, game_date DESC);
CREATE INDEX IF NOT EXISTS idx_ext_picks_pull
    ON public.external_picks (pull_id);

-- ────────────────────────────────────────────────────────────
-- Verify
-- ────────────────────────────────────────────────────────────
SELECT
    (SELECT count(*) FROM information_schema.tables
     WHERE table_schema='public' AND table_name='external_pull_log') AS log_table_exists,
    (SELECT count(*) FROM information_schema.tables
     WHERE table_schema='public' AND table_name='external_picks') AS picks_table_exists,
    (SELECT count(*) FROM pg_indexes
     WHERE schemaname='public' AND tablename IN ('external_pull_log','external_picks')) AS indexes_created;

NOTIFY pgrst, 'reload schema';
