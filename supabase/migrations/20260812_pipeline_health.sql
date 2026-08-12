-- Pipeline health dashboard (2026-08-12).
--
-- Daily metrics of how often auto-repair fires + how many critical
-- blocks the pipeline hits. Enables launch-readiness decision: don't
-- flip paywall until this shows sustained downtrend (2 weeks green =
-- OK to charge users).
--
-- Populated by:
--   Each auto-repair action in jerry_pre_publish_audit.py (writes a row per fire)
--   Each critical block that survives repair (writes row with severity=critical)
--
-- Consumed by:
--   report_pipeline_health.py (daily summary + trend)
--   App admin panel (future) — real-time health metric

CREATE TABLE IF NOT EXISTS pipeline_health_events (
  id SERIAL PRIMARY KEY,
  event_date DATE NOT NULL,
  sport TEXT NOT NULL DEFAULT 'MLB',
  event_class TEXT NOT NULL,               -- 'auto_repair' / 'critical_block' / 'stat_verifier_flag'
  rule TEXT,                                -- e.g. 'A_layer_d_jerry_reads', 'G_null_call_text'
  severity TEXT,                            -- 'info' / 'warning' / 'critical'
  count INT NOT NULL DEFAULT 1,             -- how many fires
  context JSONB,                            -- optional per-event details
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_phe_date ON pipeline_health_events (event_date DESC);
CREATE INDEX IF NOT EXISTS idx_phe_class ON pipeline_health_events (event_class, event_date DESC);
CREATE INDEX IF NOT EXISTS idx_phe_rule ON pipeline_health_events (rule, event_date DESC);

-- Daily rollup view for quick dashboard queries
CREATE OR REPLACE VIEW pipeline_health_daily AS
SELECT
  event_date,
  sport,
  event_class,
  rule,
  SUM(count) AS total_fires,
  MAX(created_at) AS last_seen
FROM pipeline_health_events
GROUP BY event_date, sport, event_class, rule
ORDER BY event_date DESC, event_class, total_fires DESC;

NOTIFY pgrst, 'reload schema';
