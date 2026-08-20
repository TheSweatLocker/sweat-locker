-- Daily surface records (2026-08-20).
--
-- Aggregate per-surface daily record + unit performance. One row per
-- (surface, date). Surfaces: sharp_card, sweat_card, ledger_chalk,
-- ledger_teased_spreads, ledger_teased_totals, ladder, dawg_of_day,
-- daily_degen, potd.
--
-- Populated by `aggregate_daily_records.py` which runs nightly after
-- all resolvers grade their picks. Enables app UI to show:
--   "Sharp Card yesterday: 4-2, +2.4u"
--   "Ledger MTD: 12-8, +3.1u"
-- Instead of computing on the fly across a bunch of tables.
--
-- Prior state: NO daily record persisted for Sharp Card at all.
-- User personally caught this on 8/20 audit.

CREATE TABLE IF NOT EXISTS public.daily_surface_records (
  id             BIGSERIAL PRIMARY KEY,
  surface        TEXT NOT NULL,          -- sharp_card | sweat_card | ledger_* | ladder | ...
  sport          TEXT NOT NULL,          -- MLB | NFL | ... | ALL (cross-sport aggregates)
  record_date    DATE NOT NULL,          -- ET calendar date
  wins           INT NOT NULL DEFAULT 0,
  losses         INT NOT NULL DEFAULT 0,
  pushes         INT NOT NULL DEFAULT 0,
  units_bet      NUMERIC NOT NULL DEFAULT 0,   -- total units risked
  units_won      NUMERIC NOT NULL DEFAULT 0,   -- net units (can be negative)
  pick_count     INT NOT NULL DEFAULT 0,
  detail         JSONB,                  -- per-pick breakdown for audit
  computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (surface, sport, record_date)
);

CREATE INDEX IF NOT EXISTS daily_surface_records_date_idx
  ON public.daily_surface_records (record_date DESC);
CREATE INDEX IF NOT EXISTS daily_surface_records_surface_idx
  ON public.daily_surface_records (surface, record_date DESC);

COMMENT ON TABLE public.daily_surface_records IS
  'Nightly-computed per-surface record + unit performance. One row per '
  '(surface, sport, date). Populated by aggregate_daily_records.py.';

NOTIFY pgrst, 'reload schema';
