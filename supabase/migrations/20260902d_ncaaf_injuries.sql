-- ncaaf_injuries table (2026-09-02) — mirrors nfl_injuries schema.
-- Populated by ncaaf_injuries_espn_pull.py nightly during CFB season.
-- Consumed by ncaaf_mc_simulator Tier F (post-launch) for QB-out penalty.

CREATE TABLE IF NOT EXISTS public.ncaaf_injuries (
  id                 BIGSERIAL PRIMARY KEY,
  season             INT NOT NULL,
  week               INT NOT NULL,
  team               TEXT NOT NULL,          -- school display name from ESPN
  player_name        TEXT NOT NULL,
  player_id          TEXT,                    -- ESPN athlete id (nullable)
  position           TEXT,                    -- QB / RB / WR / etc
  injury_status      TEXT NOT NULL,           -- Out | Doubtful | Questionable | Full
  practice_status    TEXT,
  body_part          TEXT,
  report_date        DATE NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ncaaf_injuries_unique UNIQUE (season, week, team, player_name)
);

COMMENT ON TABLE public.ncaaf_injuries IS
  '2026-09-02 CFB injury reports pulled from ESPN. Mirrors nfl_injuries shape. Post-launch consumer: ncaaf_mc_simulator Tier F QB-out penalty.';

CREATE INDEX IF NOT EXISTS ix_ncaaf_injuries_season_week
  ON public.ncaaf_injuries (season, week);
CREATE INDEX IF NOT EXISTS ix_ncaaf_injuries_team
  ON public.ncaaf_injuries (team);

ALTER TABLE public.ncaaf_injuries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ncaaf_injuries_read_public" ON public.ncaaf_injuries;
CREATE POLICY "ncaaf_injuries_read_public"
  ON public.ncaaf_injuries FOR SELECT USING (true);

GRANT SELECT ON public.ncaaf_injuries TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
