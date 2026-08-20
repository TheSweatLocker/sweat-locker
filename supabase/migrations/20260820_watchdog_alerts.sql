-- Watchdog alerts (2026-08-20).
--
-- Central table for silent-failure detection. `watchdogs.py` runs a suite
-- of checks daily (or on-demand) and writes any tripped alerts here.
-- One row per (check_name, run_date) — upsert semantics so re-runs on
-- the same day update the existing row rather than duplicate.
--
-- Severity ladder:
--   CRITICAL — something is broken NOW, needs immediate attention
--   WARNING  — trending toward broken, worth investigating today
--   INFO     — heads-up only, no action required
--
-- Design goal: catch the failure modes the user has personally been
-- the alerting layer for — ladder sitting empty for weeks, chalk trio
-- dedupe bug, primary_play staleness, source-scraper dying silently.
-- Every check here is a bug caught in this session or a known past leak.

CREATE TABLE IF NOT EXISTS public.watchdog_alerts (
  id            BIGSERIAL PRIMARY KEY,
  run_date      DATE NOT NULL,
  check_name    TEXT NOT NULL,          -- 'ladder_empty' | 'prime_hit_crash' | 'grader_coverage' | ...
  severity      TEXT NOT NULL,          -- 'CRITICAL' | 'WARNING' | 'INFO'
  message       TEXT NOT NULL,          -- human-readable one-liner
  detail        JSONB,                  -- structured payload for debugging
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at   TIMESTAMPTZ,            -- set when the check passes again
  UNIQUE (run_date, check_name)
);

CREATE INDEX IF NOT EXISTS watchdog_alerts_severity_idx
  ON public.watchdog_alerts (severity, run_date DESC)
  WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS watchdog_alerts_active_idx
  ON public.watchdog_alerts (run_date DESC)
  WHERE resolved_at IS NULL;

COMMENT ON TABLE public.watchdog_alerts IS
  'Silent-failure alerting. Populated by mlb_pipeline/watchdogs.py. '
  'Every alert is a leak the user has manually caught before — this table '
  'automates that visibility.';

NOTIFY pgrst, 'reload schema';
