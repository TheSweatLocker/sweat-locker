-- Monitoring dashboard tables (2026-08-14).
--
-- Session A of the pre-launch safety infrastructure. Three tables that
-- together let us catch systemic model/rule drift BEFORE users lose money
-- on picks generated from broken calibration.
--
-- Why this exists: today's audit found FORCE_FADE_TRAP was inverting 88%
-- winners into losers for weeks. FORCE_PASS_CONFLICT killed 63% winners
-- as skips. Both invisible without a manual deep-dive. With paying subs
-- live, silent bugs like these cost real money + trust. This infra makes
-- drift visible day-1 instead of month-1.
--
-- Sport-universal by design: every table keys on `sport`, so adding a
-- new sport is 1 config entry in compute_hit_rate_dashboard.py — no
-- schema change, no query rewrite.

-- ─── HIT_RATE_SNAPSHOTS ──────────────────────────────────────────────
--
-- Daily-computed rolling hit rates per (sport, surface, tier, window).
-- surface names the pick "type": jerry_game, jerry_prop, pipeline_prop,
-- dawg, primary_play. tier is the tier band (PRIME/STRONG/LEAN/…) or
-- NULL for surface-wide aggregates. window is the lookback in days.
--
-- Grows ~200 rows/day (7 sports × 5 surfaces × 6 tiers × 3 windows).
-- Retention: keep full history; used for month-over-month drift tracking.

CREATE TABLE IF NOT EXISTS public.hit_rate_snapshots (
  id            BIGSERIAL PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  sport         TEXT NOT NULL,
  surface       TEXT NOT NULL,
  tier          TEXT,
  window_days   INT NOT NULL,
  wins          INT NOT NULL DEFAULT 0,
  losses        INT NOT NULL DEFAULT 0,
  pushes        INT NOT NULL DEFAULT 0,
  no_action     INT NOT NULL DEFAULT 0,
  hit_rate      NUMERIC,
  sample_n      INT,
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (snapshot_date, sport, surface, tier, window_days)
);

-- Common query pattern: latest snapshot for a (sport, surface, tier, window)
CREATE INDEX IF NOT EXISTS hit_rate_snapshots_recent_idx
  ON public.hit_rate_snapshots (sport, surface, tier, window_days, snapshot_date DESC);

-- Dashboard: "show me all today's snapshots"
CREATE INDEX IF NOT EXISTS hit_rate_snapshots_date_idx
  ON public.hit_rate_snapshots (snapshot_date DESC);


-- ─── RULE_FIRE_STATS ──────────────────────────────────────────────────
--
-- Per-rule fire count + outcome per (sport, window). Sourced by scanning
-- audit_notes / short_read for [Auto-<rule>...] prefixes and cross-
-- referencing with the underlying prop_pipeline result.
--
-- Grows ~300 rows/day (7 sports × ~15 rules × 3 windows).
-- Used by alert_dashboard_anomalies.py to catch rule-level drift.

CREATE TABLE IF NOT EXISTS public.rule_fire_stats (
  id                BIGSERIAL PRIMARY KEY,
  snapshot_date     DATE NOT NULL,
  sport             TEXT NOT NULL,
  rule_name         TEXT NOT NULL,
  rule_class        TEXT,           -- 'refit_override' | 'jerry_synthesis' | 'pipeline_repair'
  window_days       INT NOT NULL,
  fires             INT NOT NULL DEFAULT 0,
  wins_when_fired   INT DEFAULT 0,
  losses_when_fired INT DEFAULT 0,
  hit_rate          NUMERIC,
  sample_n          INT,
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (snapshot_date, sport, rule_name, window_days)
);

CREATE INDEX IF NOT EXISTS rule_fire_stats_recent_idx
  ON public.rule_fire_stats (sport, rule_name, window_days, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS rule_fire_stats_flagged_idx
  ON public.rule_fire_stats (snapshot_date DESC, hit_rate)
  WHERE sample_n >= 20 AND hit_rate < 45;


-- ─── DASHBOARD_ALERTS ────────────────────────────────────────────────
--
-- Append-only alert log. Written by alert_dashboard_anomalies.py when
-- regression thresholds trip. Operator marks acknowledged=true after
-- investigating; anything unacknowledged surfaces at top of the
-- report_dashboard CLI.
--
-- Categories:
--   tier_hit_drop      : (sport, surface, tier) 7d hit% dropped >= N pp
--                         vs 30d baseline
--   rule_hit_drop      : (sport, rule_name) 7d hit% < 45% at n>=20
--   silent_failure     : a data-quality assertion tripped (from Session B)
--   sample_stall       : expected fires/day dropped to 0 (pipeline break?)
--   calibration_drift  : refit / MC / model prediction bias exceeded
--                        rolling stddev threshold

CREATE TABLE IF NOT EXISTS public.dashboard_alerts (
  id               BIGSERIAL PRIMARY KEY,
  alert_date       DATE NOT NULL,
  severity         TEXT NOT NULL CHECK (severity IN ('info','warn','critical')),
  category         TEXT NOT NULL,
  sport            TEXT,
  surface          TEXT,
  tier             TEXT,
  rule_name        TEXT,
  message          TEXT NOT NULL,
  metric_current   NUMERIC,
  metric_baseline  NUMERIC,
  metric_delta     NUMERIC,
  detail           JSONB,
  acknowledged     BOOLEAN NOT NULL DEFAULT FALSE,
  acknowledged_at  TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Common queries: (1) show all unacknowledged, (2) show today's alerts by severity
CREATE INDEX IF NOT EXISTS dashboard_alerts_unack_idx
  ON public.dashboard_alerts (created_at DESC)
  WHERE acknowledged = FALSE;

CREATE INDEX IF NOT EXISTS dashboard_alerts_by_date_severity_idx
  ON public.dashboard_alerts (alert_date DESC, severity);


-- RLS: readable by anon (dashboards can be surfaced in-app later);
--     writable by pipeline. Matches sibling monitoring tables.
ALTER TABLE public.hit_rate_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rule_fire_stats    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dashboard_alerts   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read ON public.hit_rate_snapshots;
CREATE POLICY public_read ON public.hit_rate_snapshots
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.hit_rate_snapshots;
CREATE POLICY public_write ON public.hit_rate_snapshots
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS public_read ON public.rule_fire_stats;
CREATE POLICY public_read ON public.rule_fire_stats
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.rule_fire_stats;
CREATE POLICY public_write ON public.rule_fire_stats
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS public_read ON public.dashboard_alerts;
CREATE POLICY public_read ON public.dashboard_alerts
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.dashboard_alerts;
CREATE POLICY public_write ON public.dashboard_alerts
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
