-- External source records rollup (2026-08-15 pm).
--
-- Per-source × sport × market hit-rate rollup over rolling windows.
-- Nightly job compute_external_source_records.py reads external_picks
-- (which stores per-pick source/side/odds/result), computes hit rate +
-- ROI + n over 7d/30d/90d/lifetime, writes here for lookup.
--
-- Play_of_day + Steam Room UI can then weight external sources by their
-- real track record instead of treating every handicapper equally.

CREATE TABLE IF NOT EXISTS public.external_source_records (
  id             BIGSERIAL PRIMARY KEY,
  source         TEXT NOT NULL,          -- 'cleatz', 'action', 'covers', etc.
  sport          TEXT NOT NULL,          -- 'MLB', 'NFL', 'UFC', etc.
  market         TEXT NOT NULL,          -- 'ml', 'spread', 'rl', 'total', 'ALL'
  window_days    INT  NOT NULL,          -- 7 | 30 | 90 | 9999 (lifetime)
  wins           INT NOT NULL DEFAULT 0,
  losses         INT NOT NULL DEFAULT 0,
  pushes         INT NOT NULL DEFAULT 0,
  n_graded       INT NOT NULL DEFAULT 0,
  hit_rate       NUMERIC,
  roi_pct        NUMERIC,                -- flat 1u ROI at -110 default
  computed_at    TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE (source, sport, market, window_days)
);

CREATE INDEX IF NOT EXISTS ext_src_records_lookup_idx
  ON public.external_source_records (source, sport, market, window_days);

COMMENT ON TABLE public.external_source_records IS
  'Per-handicapper hit-rate rollup. Populated nightly by compute_external_source_records.py.';

NOTIFY pgrst, 'reload schema';
