-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-03 · EXTERNAL SOR BENCHMARK
--
-- WHY
-- ---
-- Andy: "Is their SOR worth noting is much different from ours? add as
-- weekly benchmark for data."
--
-- strengthofrecord.com publishes a Strength of Record ranking for FBS with
-- a clean public JSON API (api.strengthofrecord.com/snapshots/latest, no
-- auth, robots.txt Allow: /). We are NOT adopting it as an input -- it
-- carries no SOS at all, it is CFBD-derived like ours, and its SOR
-- correlates +0.971 with raw win% against our +0.914, i.e. it is closer to
-- being the record than ours is. Full assessment in the session notes.
--
-- What it IS good for is the job CFBD's own `sos` field was supposed to do
-- and cannot (null on 0 of 808 rows, 2021-2026): an outside reference point.
-- Our SOS/SOR has no external check of any kind, which is why a deleted
-- opponent went unnoticed until Andy eyeballed Miami (OH) at rank 2 of 137.
-- A second opinion recorded weekly would have shown that as a divergence.
--
-- ONE ROW PER (season, week, source). The external snapshot is immutable
-- once published and carries its own content hash, so a re-run is an upsert
-- and never a duplicate.
--
-- DELIBERATELY NOT team_computed_stats. That table is UNIONed into the
-- team_stats_rolling VIEW, which GameDetailV2's TeamStatsCard selects '*'
-- from -- so a stat_key landing there appears on the app card automatically.
-- An unvetted third-party metric must not reach a subscriber as though it
-- were ours; it lives in its own table until someone decides otherwise.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS public.sor_benchmark (
  id              bigserial PRIMARY KEY,
  sport           text        NOT NULL DEFAULT 'NCAAF',
  season          integer     NOT NULL,
  week            integer     NOT NULL,
  source          text        NOT NULL,          -- 'strengthofrecord.com'
  -- provenance straight from their payload, so a disagreement can be
  -- replayed against the exact snapshot that produced it
  source_hash     text,
  source_generated_at timestamptz,
  teams_external  integer,
  teams_ours      integer,
  teams_matched   integer,
  teams_unmatched jsonb,                          -- names we could not join
  -- the comparison itself
  corr_vs_ours        numeric,                    -- their SOR vs our SOR
  corr_theirs_vs_winpct numeric,                  -- how much is just record
  corr_ours_vs_winpct   numeric,
  rank_mean_abs_delta numeric,                    -- mean |their rank - ours|
  top10_overlap       integer,
  biggest_disagreements jsonb,                    -- [{team, theirs, ours, record}]
  computed_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sor_benchmark_uniq UNIQUE (sport, season, week, source)
);

COMMENT ON TABLE public.sor_benchmark IS
  'Weekly external Strength-of-Record reference vs our computed SOR. A '
  'benchmark only -- never an input to a pick, and deliberately outside '
  'team_computed_stats so it cannot surface on the app card via the '
  'team_stats_rolling view. Written by benchmark_external_sor.py.';
COMMENT ON COLUMN public.sor_benchmark.corr_theirs_vs_winpct IS
  'If this sits near 1.0 the external metric is mostly re-expressing the '
  'win-loss record. Measured +0.971 on 2026 week 5 against our +0.914.';
COMMENT ON COLUMN public.sor_benchmark.teams_unmatched IS
  'Team names present externally but absent from ours. A silent name '
  'mismatch DROPS a real team rather than erroring -- the same alias trap '
  'documented in compute_schedule_strength.py (App State / UConn / '
  'Hawai''i). Recorded so the count is visible rather than inferred.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — read it back; a successful DDL is not proof the API can see it.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT season, week, source, teams_matched, corr_vs_ours,
--        corr_theirs_vs_winpct, corr_ours_vs_winpct, rank_mean_abs_delta
--   FROM public.sor_benchmark
--  ORDER BY season DESC, week DESC;
