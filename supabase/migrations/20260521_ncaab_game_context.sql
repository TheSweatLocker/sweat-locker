-- NCAAB game context — pick-time state for upcoming/current games
-- ============================================================
-- Mirrors mlb_game_context. Server-side ncaab_game_context.py (Phase 2,
-- queued for July) populates this table daily with:
--   - Today's slate from the Odds API (canonical team names via ncaab_team_aliases)
--   - KenPom features (adj_em, adj_oe, adj_de, pace) joined from ncaab_team_stats
--   - Market lines (open + close, with same lock semantics as MLB)
--   - Model projections (projected_total, projected_spread)
--   - Sweat score + tier + primary_play (compute_primary_play analog)
--
-- App reads sweat_score / sweat_tier / primary_play directly from this
-- table — no client-side scoring (the "backside dictates, app renders"
-- rule applies here just like MLB).
--
-- Spreads/totals/ML only for v1.0 — no NCAAB player props (limited
-- market coverage + per-user explicitly scoped out 2026-05-21).
--
-- Apply via Supabase SQL editor. Idempotent.

CREATE TABLE IF NOT EXISTS ncaab_game_context (
  id                              BIGSERIAL PRIMARY KEY,
  game_id                         TEXT UNIQUE,
  game_date                       DATE NOT NULL,
  season                          TEXT,

  -- Teams (canonical via ncaab_team_aliases)
  home_team                       TEXT NOT NULL,
  away_team                       TEXT NOT NULL,
  conference_home                 TEXT,
  conference_away                 TEXT,

  -- KenPom efficiency snapshot at pick time
  home_adj_em                     NUMERIC,
  away_adj_em                     NUMERIC,
  adj_em_gap                      NUMERIC,      -- home_adj_em - away_adj_em (positive = home better)
  home_adj_oe                     NUMERIC,
  away_adj_oe                     NUMERIC,
  home_adj_de                     NUMERIC,
  away_adj_de                     NUMERIC,
  pace_avg                        NUMERIC,      -- avg of both team tempos
  home_tempo                      NUMERIC,
  away_tempo                      NUMERIC,

  -- Four factors (key NCAAB drivers)
  home_efg_o                      NUMERIC,
  away_efg_o                      NUMERIC,
  home_efg_d                      NUMERIC,
  away_efg_d                      NUMERIC,
  home_to_o                       NUMERIC,
  away_to_o                       NUMERIC,
  home_or_o                       NUMERIC,      -- offensive rebound %
  away_or_o                       NUMERIC,
  home_ftr_o                      NUMERIC,
  away_ftr_o                      NUMERIC,

  -- Bart Torvik / supplementary (optional)
  home_bart_em                    NUMERIC,
  away_bart_em                    NUMERIC,

  -- Form / situational
  home_record                     TEXT,
  away_record                     TEXT,
  home_l10                        TEXT,
  away_l10                        TEXT,
  home_days_rest                  INTEGER,
  away_days_rest                  INTEGER,

  -- Market lines (open + close lock semantics same as MLB)
  open_total                      NUMERIC,
  close_total                     NUMERIC,
  open_spread                     NUMERIC,
  close_spread                    NUMERIC,      -- home perspective: negative = home favored
  home_ml_open                    INTEGER,
  away_ml_open                    INTEGER,
  home_ml_close                   INTEGER,
  away_ml_close                   INTEGER,

  -- Model projections (KenPom-driven + pace + venue + B2B + injury)
  projected_total                 NUMERIC,
  projected_spread                NUMERIC,      -- positive = home wins by X
  model_pred_home_points          NUMERIC,
  model_pred_away_points          NUMERIC,

  -- Confluence (mirror of MLB signal_confluence_net)
  signal_confluence_net           INTEGER,
  signal_confluence_breakdown     JSONB,

  -- Venue + tournament context
  venue                           TEXT,
  game_time_et                    TIMESTAMPTZ,
  is_tournament                   BOOLEAN DEFAULT FALSE,
  tournament_round                TEXT,
  is_neutral_site                 BOOLEAN DEFAULT FALSE,

  -- Server-computed sweat score + primary play (app reads directly)
  sweat_score                     INTEGER,
  sweat_tier                      TEXT,         -- 'PRIME' | 'STRONG' | 'LIGHT' | 'PASS'
  primary_play                    JSONB,        -- {type, tier, label, sub, signal_floor, audit_note}

  -- Bookkeeping
  fetched_at                      TIMESTAMPTZ DEFAULT NOW(),
  updated_at                      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ncaab_game_context_date
  ON ncaab_game_context(game_date DESC);
CREATE INDEX IF NOT EXISTS idx_ncaab_game_context_tier
  ON ncaab_game_context(sweat_tier) WHERE sweat_tier IN ('PRIME', 'STRONG');

-- Server-side RPC mirror of get_todays_pipeline_props pattern.
-- Returns NCAAB context for ET-today only. Client never derives slate date.
CREATE OR REPLACE FUNCTION get_todays_ncaab_context()
RETURNS SETOF ncaab_game_context
LANGUAGE sql STABLE
AS $$
  SELECT * FROM ncaab_game_context
  WHERE game_date = (NOW() AT TIME ZONE 'America/New_York')::date
  ORDER BY sweat_score DESC NULLS LAST, game_time_et ASC;
$$;
