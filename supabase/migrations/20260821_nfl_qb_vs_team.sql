-- nfl_qb_vs_team — QB career + recent stats vs specific opponent defenses.
--
-- 8/21 user directive: apply MLB pitcher_vs_team framework cross-sport.
-- For NFL, QB career stats vs specific defense is the highest-leverage
-- missing factor. Existing nfl_game_context has h2h team-level trends
-- but no per-QB-vs-opponent history.
--
-- Data source: nflverse play-by-play + weekly QB gamelog. Backfill
-- script: nfl_qb_vs_team_backfill.py (see follow-up commit).
--
-- Schema mirrors MLB's mlb_game_context vs-team fields:
--   career: aggregate over all starts vs this opponent
--   recent: last 3 starts vs this opponent

CREATE TABLE IF NOT EXISTS public.nfl_qb_vs_team (
  id                    BIGSERIAL PRIMARY KEY,
  qb_id                 TEXT NOT NULL,           -- nflverse player_id
  qb_name               TEXT NOT NULL,
  opponent_team         TEXT NOT NULL,           -- 3-letter code (KC, BUF, etc)
  -- Career totals
  career_starts         INT DEFAULT 0,
  career_pass_yds       INT DEFAULT 0,
  career_pass_td        INT DEFAULT 0,
  career_int            INT DEFAULT 0,
  career_completions    INT DEFAULT 0,
  career_attempts       INT DEFAULT 0,
  career_sacks_taken    INT DEFAULT 0,
  career_rush_yds       INT DEFAULT 0,
  career_rush_td        INT DEFAULT 0,
  career_wins           INT DEFAULT 0,
  career_losses         INT DEFAULT 0,
  -- Rate stats (career)
  career_cmp_pct        NUMERIC,                 -- completions/attempts
  career_yds_per_att    NUMERIC,
  career_td_int_ratio   NUMERIC,
  career_qb_rating      NUMERIC,                 -- passer rating (0-158.3)
  -- Recent (last 3 vs this opp)
  recent_n_starts       INT DEFAULT 0,
  recent_pass_yds_avg   NUMERIC,
  recent_pass_td_avg    NUMERIC,
  recent_int_avg        NUMERIC,
  recent_cmp_pct        NUMERIC,
  recent_qb_rating      NUMERIC,
  recent_wins           INT DEFAULT 0,
  recent_losses         INT DEFAULT 0,
  -- Meta
  last_faced_date       DATE,
  last_updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS nfl_qb_vs_team_uniq
  ON public.nfl_qb_vs_team (qb_id, opponent_team);

CREATE INDEX IF NOT EXISTS nfl_qb_vs_team_name_idx
  ON public.nfl_qb_vs_team (qb_name);

COMMENT ON TABLE public.nfl_qb_vs_team IS
  'QB career + recent gamelog aggregated per opponent defense. Feeds NFL prop signals for pass_yds/pass_td/int props.';

NOTIFY pgrst, 'reload schema';
