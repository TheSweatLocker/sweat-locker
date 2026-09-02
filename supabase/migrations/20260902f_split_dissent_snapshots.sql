-- split_dissent_snapshots (2026-09-02)
-- Per-game per-market dissent tags computed nightly from public_splits_v2.
-- Consumed by Vault Match dissent patterns (compute_sport_patterns.py).
--
-- Row = one game/market's sharp-side agreement pattern.
-- agreement:
--   'TRIPLE' — 3+ sources agree on sharp side
--   'MAJ_2/3' — 2 sources agree, 1 dissents
--   'SPLIT_1v1' — 2 sources disagree, no majority
--   'SOLO' — only 1 source present (unverifiable)
-- dissenter: source name that dissents from majority (null unless MAJ_2/3)
-- majority_side: HOME/AWAY/OVER/UNDER — the side majority sources call sharp

CREATE TABLE IF NOT EXISTS public.split_dissent_snapshots (
  sport               TEXT NOT NULL,
  game_id             TEXT NOT NULL,
  market              TEXT NOT NULL,          -- ml / rl / total
  agreement           TEXT NOT NULL,          -- TRIPLE / MAJ_2/3 / SPLIT_1v1 / SOLO
  dissenter           TEXT,                    -- source name if MAJ_2/3, else NULL
  majority_side       TEXT,                    -- HOME/AWAY/OVER/UNDER
  sources_present     TEXT[],                  -- e.g. {so, cz, fr}
  n_sources           INT NOT NULL,
  snapshot_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (sport, game_id, market)
);

CREATE INDEX IF NOT EXISTS ix_split_dissent_snapshots_sport
  ON public.split_dissent_snapshots (sport);
CREATE INDEX IF NOT EXISTS ix_split_dissent_snapshots_agreement
  ON public.split_dissent_snapshots (sport, agreement);

ALTER TABLE public.split_dissent_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "split_dissent_snapshots_read_public"
  ON public.split_dissent_snapshots;
CREATE POLICY "split_dissent_snapshots_read_public"
  ON public.split_dissent_snapshots FOR SELECT USING (true);

GRANT SELECT ON public.split_dissent_snapshots TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
