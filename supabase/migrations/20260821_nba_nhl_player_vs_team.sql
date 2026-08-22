-- Player-vs-team career + recent aggregations for NBA + NHL props.
--
-- Mirror of NFL nfl_qb_vs_team pattern shipped earlier 8/21. Powers
-- positional matchup signals like "player scores 20+ pts vs this defense
-- career" or "goalie SV% .930+ vs this team career".
--
-- Backfill scripts: nba_player_vs_team_backfill.py + nhl_player_vs_team_backfill.py
-- (design pending — need to pull from public data sources like nba-api,
-- Basketball Reference, MoneyPuck, NHL Stats API).

-- ═══════════════════════════════════════════════════════════════════════
-- NBA — one row per (player, opponent) with career + recent aggregates
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.nba_player_vs_team (
  id                    BIGSERIAL PRIMARY KEY,
  player_id             TEXT NOT NULL,
  player_name           TEXT NOT NULL,
  opponent_team         TEXT NOT NULL,
  -- Career aggregates (across all games vs this opponent)
  career_games          INT DEFAULT 0,
  career_pts_avg        NUMERIC,
  career_reb_avg        NUMERIC,
  career_ast_avg        NUMERIC,
  career_stl_avg        NUMERIC,
  career_blk_avg        NUMERIC,
  career_3pm_avg        NUMERIC,
  career_minutes_avg    NUMERIC,
  career_fg_pct         NUMERIC,
  career_3p_pct         NUMERIC,
  career_ft_pct         NUMERIC,
  -- Recent (last 5 games vs this opp)
  recent_n_games        INT DEFAULT 0,
  recent_pts_avg        NUMERIC,
  recent_reb_avg        NUMERIC,
  recent_ast_avg        NUMERIC,
  recent_minutes_avg    NUMERIC,
  recent_fg_pct         NUMERIC,
  -- Meta
  last_faced_date       DATE,
  last_updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS nba_player_vs_team_uniq
  ON public.nba_player_vs_team (player_id, opponent_team);
CREATE INDEX IF NOT EXISTS nba_player_vs_team_name_idx
  ON public.nba_player_vs_team (player_name);

COMMENT ON TABLE public.nba_player_vs_team IS
  'NBA player career + recent stats aggregated per opposing defense. Feeds prop signals for pts/reb/ast/3PM/PRA overs and unders.';

-- ═══════════════════════════════════════════════════════════════════════
-- NHL — skater or goalie vs team
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.nhl_player_vs_team (
  id                    BIGSERIAL PRIMARY KEY,
  player_id             TEXT NOT NULL,
  player_name           TEXT NOT NULL,
  position              TEXT,                    -- G / D / F / C / LW / RW
  opponent_team         TEXT NOT NULL,
  -- Skater career (for shooters)
  career_games          INT DEFAULT 0,
  career_goals_avg      NUMERIC,
  career_assists_avg    NUMERIC,
  career_points_avg     NUMERIC,
  career_shots_avg      NUMERIC,
  career_pp_pts_avg     NUMERIC,
  career_toi_avg        NUMERIC,                 -- time on ice (minutes)
  -- Goalie career (position=G rows)
  career_starts         INT,
  career_sv_pct         NUMERIC,
  career_gaa            NUMERIC,
  career_saves_avg      NUMERIC,
  career_shots_faced_avg NUMERIC,
  career_wins           INT,
  career_losses         INT,
  -- Recent (last 5)
  recent_n_games        INT DEFAULT 0,
  recent_points_avg     NUMERIC,                 -- skater
  recent_shots_avg      NUMERIC,                 -- skater
  recent_sv_pct         NUMERIC,                 -- goalie
  recent_saves_avg      NUMERIC,                 -- goalie
  -- Meta
  last_faced_date       DATE,
  last_updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS nhl_player_vs_team_uniq
  ON public.nhl_player_vs_team (player_id, opponent_team);
CREATE INDEX IF NOT EXISTS nhl_player_vs_team_name_idx
  ON public.nhl_player_vs_team (player_name);

COMMENT ON TABLE public.nhl_player_vs_team IS
  'NHL skater OR goalie career + recent stats per opposing team. Position field distinguishes skater rows (goals/assists/points/shots) from goalie rows (SV%/GAA/wins). Feeds prop signals for player shots/goals/assists overs and goalie SV% / saves overs.';

NOTIFY pgrst, 'reload schema';
