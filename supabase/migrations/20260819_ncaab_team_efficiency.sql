-- NCAAB efficiency panel materialization (2026-08-19).
--
-- Persistent per-team materialization of the 3-source efficiency panel
-- (kenpom + torvik + haslam) that ncaab_panel_predictor.py currently
-- computes live per game and stashes inside
-- ncaab_game_context.panel_prediction JSONB. Materializing to a proper
-- table gives us:
--   * A canonical "current efficiency ratings" store for every team,
--     not just teams appearing in tonight's slate.
--   * A stable target for offseason cohort backfills and dashboard
--     rollups (they can JOIN instead of parsing JSONB).
--   * Defense-in-depth: box-score-derived PPG + margin columns give us
--     a fallback prior if all three panel sources fail to scrape.
--
-- Naming rule: INTERNAL only may reference component source systems
-- by name. USER-FACING copy (Jerry, app cards, external picks) must
-- call this "efficiency model" or "efficiency panel" — enforced by
-- project rule feedback_no_kenpom_attribution.md.
--
-- Populated by mlb_pipeline/ncaab_efficiency_model.py — cron weekly
-- during season, ad-hoc during offseason.

CREATE TABLE IF NOT EXISTS public.ncaab_team_efficiency (
  team              TEXT        NOT NULL,
  season            TEXT        NOT NULL,          -- '2024-25', '2025-26'
  games_played      INT         NOT NULL DEFAULT 0,
  wins              INT         NOT NULL DEFAULT 0,
  losses            INT         NOT NULL DEFAULT 0,
  home_wins         INT         NOT NULL DEFAULT 0,
  home_losses       INT         NOT NULL DEFAULT 0,
  away_wins         INT         NOT NULL DEFAULT 0,
  away_losses       INT         NOT NULL DEFAULT 0,

  -- Raw scoring
  points_for        INT         NOT NULL DEFAULT 0,
  points_against    INT         NOT NULL DEFAULT 0,
  ppg_for           NUMERIC(5,2),
  ppg_against       NUMERIC(5,2),
  avg_margin        NUMERIC(5,2),                  -- ppg_for - ppg_against

  -- Panel projections (canonical) — mean across available rating systems
  -- est_off/def/net_rating carry the panel means when available; otherwise
  -- fall back to self-derived (ppg / tempo * 100).
  est_tempo         NUMERIC(5,2),                  -- panel mean or league fallback
  est_off_rating    NUMERIC(6,2),
  est_def_rating    NUMERIC(6,2),
  est_net_rating    NUMERIC(6,2),

  -- Provenance
  data_source       TEXT        DEFAULT 'panel_kp_tv_hm+ncaab_game_results',
  tempo_source      TEXT,                          -- 'panel_mean' | 'league_avg_fallback'
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  PRIMARY KEY (team, season)
);

CREATE INDEX IF NOT EXISTS ncaab_team_efficiency_season_idx
  ON public.ncaab_team_efficiency (season);
CREATE INDEX IF NOT EXISTS ncaab_team_efficiency_net_idx
  ON public.ncaab_team_efficiency (season, est_net_rating DESC NULLS LAST);

COMMENT ON TABLE public.ncaab_team_efficiency IS
  'Season-cumulative offensive/defensive efficiency for each NCAAB team, '
  'derived from ncaab_game_results (self-computed, no external API). '
  'Populated by ncaab_efficiency_model.py. Runs weekly during season.';

NOTIFY pgrst, 'reload schema';
