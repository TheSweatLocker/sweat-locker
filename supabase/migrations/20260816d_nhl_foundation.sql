-- NHL foundation — game context + game results tables (2026-08-16).
--
-- Mirrors nfl_game_context/results shape. Written by nhl_game_context.py
-- (to be built) which pulls schedule + odds + goalie stats + team stats
-- and computes context per game.
--
-- Season starts Oct 7 2026. Foundation shipped Aug 16 so we have 6
-- weeks to build enrichment cron + models + backfill.

CREATE TABLE IF NOT EXISTS public.nhl_game_context (
  id                 BIGSERIAL PRIMARY KEY,
  game_id            TEXT NOT NULL UNIQUE,
  game_date          DATE NOT NULL,
  season             INT,

  -- Teams
  home_team          TEXT NOT NULL,           -- canonical: 'Toronto Maple Leafs' etc.
  away_team          TEXT NOT NULL,
  home_team_abbrev   TEXT,                    -- 'TOR' / 'MTL' etc.
  away_team_abbrev   TEXT,

  -- Schedule metadata
  commence_time      TIMESTAMPTZ,
  venue              TEXT,
  is_neutral_site    BOOLEAN DEFAULT FALSE,

  -- Rest / travel / fatigue
  home_rest_days     INT,
  away_rest_days     INT,
  home_back_to_back  BOOLEAN,
  away_back_to_back  BOOLEAN,
  home_travel_km     NUMERIC,
  away_travel_km     NUMERIC,
  home_consecutive_road_games  INT,
  away_consecutive_road_games  INT,

  -- Goalie starters (biggest per-game variable in NHL)
  home_goalie        TEXT,
  away_goalie        TEXT,
  home_goalie_confirmed  BOOLEAN DEFAULT FALSE,
  away_goalie_confirmed  BOOLEAN DEFAULT FALSE,
  -- Goalie stats (season)
  home_goalie_sv_pct         NUMERIC,           -- 0-1
  home_goalie_gsaa           NUMERIC,           -- goals saved above avg
  home_goalie_last5_sv_pct   NUMERIC,           -- L5 form
  away_goalie_sv_pct         NUMERIC,
  away_goalie_gsaa           NUMERIC,
  away_goalie_last5_sv_pct   NUMERIC,

  -- Team analytics (from MoneyPuck / hockey-reference / NHL API)
  home_xgf_per60     NUMERIC,                  -- expected goals for per 60
  home_xga_per60     NUMERIC,                  -- expected goals against per 60
  home_high_danger_for   NUMERIC,              -- high-danger chances for
  home_high_danger_against NUMERIC,
  home_pp_pct        NUMERIC,                  -- power play %
  home_pk_pct        NUMERIC,                  -- penalty kill %
  home_5v5_cf        NUMERIC,                  -- 5v5 corsi for %
  home_l10_goals_per_game    NUMERIC,          -- rolling scoring form
  home_l10_goals_against_per_game  NUMERIC,
  away_xgf_per60     NUMERIC,
  away_xga_per60     NUMERIC,
  away_high_danger_for   NUMERIC,
  away_high_danger_against NUMERIC,
  away_pp_pct        NUMERIC,
  away_pk_pct        NUMERIC,
  away_5v5_cf        NUMERIC,
  away_l10_goals_per_game    NUMERIC,
  away_l10_goals_against_per_game  NUMERIC,

  -- Market (from OddsAPI)
  home_ml_open       INT,
  away_ml_open       INT,
  home_ml_close      INT,
  away_ml_close      INT,
  open_puckline      NUMERIC,                  -- home puckline (-1.5 favored)
  close_puckline     NUMERIC,
  open_total         NUMERIC,                  -- game total (typically 5.5 / 6 / 6.5)
  close_total        NUMERIC,

  -- Public splits (OC / FR / Cleatz)
  oddscrowd_snapshot JSONB,

  -- Model outputs (built later)
  projected_home_goals  NUMERIC,
  projected_away_goals  NUMERIC,
  projected_total       NUMERIC,
  projected_spread      NUMERIC,               -- home_goals - away_goals expectation
  mc_probabilities   JSONB,                    -- {'home_win': 0.55, 'over_prob': 0.48, etc.}
  panel_pred_total   NUMERIC,
  panel_pred_spread  NUMERIC,

  -- Cohort / signal aggregates
  signal_confluence_net       INT,
  signal_confluence_home_lean INT,
  signal_confluence_breakdown JSONB,

  -- Primary play (written by ensemble scorer)
  primary_play       JSONB,
  sweat_score        INT,
  sweat_tier         TEXT,

  -- Timestamps
  computed_at        TIMESTAMPTZ DEFAULT NOW(),
  fetched_at         TIMESTAMPTZ,
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS nhl_ctx_date_idx ON public.nhl_game_context (game_date);
CREATE INDEX IF NOT EXISTS nhl_ctx_teams_idx ON public.nhl_game_context (home_team, away_team);


CREATE TABLE IF NOT EXISTS public.nhl_game_results (
  id            BIGSERIAL PRIMARY KEY,
  game_id       TEXT NOT NULL UNIQUE,
  game_date     DATE NOT NULL,
  home_team     TEXT NOT NULL,
  away_team     TEXT NOT NULL,
  home_score    INT,
  away_score    INT,
  total_goals   INT,
  home_win      BOOLEAN,
  went_to_ot    BOOLEAN DEFAULT FALSE,
  went_to_so    BOOLEAN DEFAULT FALSE,          -- shootout
  resolved_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS nhl_results_date_idx ON public.nhl_game_results (game_date);


-- NHL props table (analog of nfl_props, mlb_pipeline_props)
CREATE TABLE IF NOT EXISTS public.nhl_props (
  prop_id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  game_id            TEXT NOT NULL,
  game_date          DATE NOT NULL,
  season             INT,
  home_team          TEXT,
  away_team          TEXT,
  player_id          TEXT,
  player_name        TEXT NOT NULL,
  team               TEXT NOT NULL,
  opponent_team      TEXT NOT NULL,
  position           TEXT,                       -- F / D / G
  prop_type          TEXT NOT NULL,              -- shots_on_goal / points / assists / goals / saves / blocked_shots
  pick_side          TEXT NOT NULL,              -- OVER / UNDER / YES / NO
  pick_line          NUMERIC NOT NULL,
  odds_american      INT,
  projected          NUMERIC,
  edge               NUMERIC,
  l5_avg             NUMERIC,                    -- 5-game rolling
  season_avg         NUMERIC,
  opp_rank           INT,
  tier               TEXT,
  conviction         INT,
  signals            JSONB,
  result             TEXT,                       -- W / L / P
  actual_value       NUMERIC,
  resolved_at        TIMESTAMPTZ,
  computed_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (game_id, player_name, prop_type, pick_side)
);

CREATE INDEX IF NOT EXISTS nhl_props_date_idx ON public.nhl_props (game_date);
CREATE INDEX IF NOT EXISTS nhl_props_tier_idx ON public.nhl_props (tier);
CREATE INDEX IF NOT EXISTS nhl_props_result_idx ON public.nhl_props (result) WHERE result IS NOT NULL;

COMMENT ON TABLE public.nhl_game_context IS
  'NHL game context — schedule + goalies + team analytics + market + models. Written nightly by nhl_game_context.py (TBD).';
COMMENT ON TABLE public.nhl_game_results IS
  'NHL game results — home/away score, OT/SO flags. Written by nhl_resolver.py (TBD).';
COMMENT ON TABLE public.nhl_props IS
  'NHL player props — shots, points, saves, blocked_shots. Same schema shape as nfl_props / mlb_pipeline_props.';

NOTIFY pgrst, 'reload schema';
