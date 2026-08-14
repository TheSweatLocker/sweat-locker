-- Data quality events log (2026-08-14).
--
-- Session B of the pre-launch safety infrastructure. Complements Session A's
-- outcome-based monitoring (hit_rate_snapshots + rule_fire_stats) with
-- input-based monitoring — catches silent DATA bugs BEFORE they propagate
-- into picks.
--
-- The Drohan 2.7 IP bug from earlier today was silent for months: MLB API
-- returned a valid number in a valid field, downstream trusted it. This
-- table logs every assertion trip so we see silent bugs on day 1 instead
-- of month 3.
--
-- Sport-universal by design: sport + source columns key each event.
-- Written by the data_quality.py assertion library any time a check fails.
-- Read by check_data_quality_daily.py aggregator which promotes recurring
-- failures to dashboard_alerts.
--
-- Categories (via `check_class`):
--   fetch_shape        : external API response missing/malformed
--                        e.g., MLB API returned empty splits when we
--                        expected a game log for a starting pitcher
--   fetch_range        : value out of realistic bounds
--                        e.g., IP < 0 or IP > 9 for a single game
--   fetch_ordering     : list wasn't sorted as expected
--                        e.g., "splits[0] should be most recent" fails
--                        when the API returns oldest-first
--   transform_range    : our own computation produced impossible value
--                        e.g., projected_total = 200 for an NFL game
--   transform_nan      : NaN or infinity appeared in a numeric field
--   write_null_regression : column that's always populated came up NULL
--   write_count_drop   : row count for today dropped >50% below rolling avg
--   cross_check_diff   : two independent sources disagree materially
--                        e.g., mlb_game_context.away_last_ip vs MLB API
--
-- Retention: keep 90 days of raw events. Recurring failures (n>=3 in 24h
-- for the same signature) get promoted to a dashboard_alert, which persists
-- until acknowledged.

CREATE TABLE IF NOT EXISTS public.data_quality_events (
  id            BIGSERIAL PRIMARY KEY,
  event_ts      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sport         TEXT,
  source        TEXT NOT NULL,     -- calling script: 'game_context.py', 'nfl_mc_simulator.py', etc.
  check_class   TEXT NOT NULL,     -- see comment above
  check_name    TEXT NOT NULL,     -- specific assertion label
                                    -- e.g., 'pitcher_last_outing.innings_range'
  severity      TEXT NOT NULL DEFAULT 'warn'
                CHECK (severity IN ('info','warn','critical')),
  message       TEXT NOT NULL,
  context       JSONB              -- structured detail: {pitcher_id, expected, actual, ...}
);

-- Common query: "what tripped today?"
CREATE INDEX IF NOT EXISTS data_quality_events_recent_idx
  ON public.data_quality_events (event_ts DESC);

-- Aggregation: "how many times did check X trip in 24h?"
CREATE INDEX IF NOT EXISTS data_quality_events_signature_idx
  ON public.data_quality_events (source, check_name, event_ts DESC);

-- Alert candidate scan: "critical events in last 24h"
CREATE INDEX IF NOT EXISTS data_quality_events_critical_idx
  ON public.data_quality_events (event_ts DESC)
  WHERE severity = 'critical';

-- RLS: readable by anon, writable by pipeline
ALTER TABLE public.data_quality_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.data_quality_events;
CREATE POLICY public_read ON public.data_quality_events
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS public_write ON public.data_quality_events;
CREATE POLICY public_write ON public.data_quality_events
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
