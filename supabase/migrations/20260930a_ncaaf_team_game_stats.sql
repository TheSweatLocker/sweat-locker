-- ════════════════════════════════════════════════════════════════════════
-- ncaaf_team_game_stats — PER-GAME team stats, one row per (game, team)
--
-- Andy 2026-09-30: "there has to be a trail to getting that data to improve
-- projection across both football nfl and ncaaf."
--
-- WHY THIS TABLE EXISTS
-- Measured 09-30 (project_pit_reconstruction_ceiling_930): a point-in-time
-- opponent-adjusted rating built from FINAL SCORES cannot beat or even add to
-- the closing line. Out-of-sample on 113 NCAAF games:
--
--     raw closing line      9.87   <- the bar
--     rating only          18.58
--     market + rating      13.36   (3.49 WORSE than the line)
--
-- and a ridge sweep confirmed the fit was not at fault: as regularisation
-- rises the market coefficient goes to 1.0 and the rating coefficient
-- collapses to 0.48, converging toward the line without beating it.
--
-- The reason is structural. ncaaf_game_results carries SCORES ONLY, and the
-- final score is exactly what the closing line has already absorbed, so a
-- reconstruction from it can only re-derive the market. There was no missing
-- weighting to solve for -- there was a missing input.
--
-- This table is that input: CFBD /stats/game/advanced (per-game EPA, success
-- rate, explosiveness, line yards, stuff rate) plus /games/teams (yards,
-- turnovers, possession time, third-down) plus /games (start date, and CFBD's
-- own PRE-GAME Elo).
--
-- WHY PER-GAME AND NOT SEASON-LEVEL
-- Every existing CFBD pull we have (ncaaf_stats_pull.py) hits
-- /stats/season/advanced, which returns CURRENT-STATE season aggregates. Join
-- one of those to a week-1 game and the model sees weeks 1-4. That is the leak
-- that produced a fake NCAAF 67.8% z=+4.07 on 09-29
-- (project_rolling_stats_leak_trap_929). Per-game rows are immutable once the
-- game is final, so any as-of-date aggregate can be REBUILT from them without
-- a snapshot and without leakage.
--
-- pregame_elo is worth calling out: CFBD computes it BEFORE the game, so it is
-- leak-free by construction and is a ready-made rating to test against the
-- hand-rolled SRS that just failed.
--
-- GRAIN: one row per (cfbd_game_id, team). Both teams of a game appear, each
-- with its own offense/defense split, which is what makes opponent adjustment
-- possible.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS public.ncaaf_team_game_stats (
  cfbd_game_id      bigint      NOT NULL,
  team              text        NOT NULL,
  opponent          text,
  season            integer     NOT NULL,
  season_type       text,
  week              integer,
  -- start_date is the CFBD kickoff (UTC). game_date is the ET calendar day and
  -- is what point-in-time rebuilds order on, because our own tables key on ET
  -- and the UTC/ET split has already cost us duplicate rows once
  -- (dedupe_ncaaf_results.py).
  start_date        timestamptz,
  game_date         date,
  home_away         text,
  is_neutral        boolean,
  conference_game   boolean,
  points            integer,
  opp_points        integer,

  -- CFBD pre-game Elo: computed BEFORE kickoff, so leak-free by construction.
  pregame_elo       numeric,
  opp_pregame_elo   numeric,

  -- /stats/game/advanced — offense
  off_plays             integer,
  off_drives            integer,
  off_ppa               numeric,   -- CFBD 'ppa' == EPA per play
  off_total_ppa         numeric,
  off_success_rate      numeric,
  off_explosiveness     numeric,
  off_power_success     numeric,
  off_stuff_rate        numeric,
  off_line_yards        numeric,
  off_second_level_yards numeric,
  off_open_field_yards  numeric,
  off_rushing_plays     integer,
  off_passing_plays     integer,
  off_std_downs_ppa     numeric,
  off_pass_downs_ppa    numeric,

  -- /stats/game/advanced — defense (same shape, allowed rather than gained)
  def_plays             integer,
  def_drives            integer,
  def_ppa               numeric,
  def_total_ppa         numeric,
  def_success_rate      numeric,
  def_explosiveness     numeric,
  def_power_success     numeric,
  def_stuff_rate        numeric,
  def_line_yards        numeric,
  def_second_level_yards numeric,
  def_open_field_yards  numeric,
  def_rushing_plays     integer,
  def_passing_plays     integer,
  def_std_downs_ppa     numeric,
  def_pass_downs_ppa    numeric,

  -- /games/teams — volumetric
  total_yards           integer,
  net_passing_yards     integer,
  rushing_yards         integer,
  yards_per_pass        numeric,
  yards_per_rush        numeric,
  first_downs           integer,
  turnovers             integer,
  fumbles_lost          integer,
  passes_intercepted    integer,
  sacks                 numeric,
  tackles_for_loss      numeric,
  penalties_yards       integer,
  possession_seconds    integer,
  third_down_conv       integer,
  third_down_att        integer,
  fourth_down_conv      integer,
  fourth_down_att       integer,

  source            text        DEFAULT 'cfbd',
  fetched_at        timestamptz DEFAULT now(),

  CONSTRAINT ncaaf_team_game_stats_pkey PRIMARY KEY (cfbd_game_id, team)
);

-- The point-in-time rebuild scans "every game for this team before date D",
-- so (team, game_date) is the access path that matters.
CREATE INDEX IF NOT EXISTS ncaaf_tgs_team_date_idx
  ON public.ncaaf_team_game_stats (team, game_date);
CREATE INDEX IF NOT EXISTS ncaaf_tgs_season_week_idx
  ON public.ncaaf_team_game_stats (season, week);
CREATE INDEX IF NOT EXISTS ncaaf_tgs_date_idx
  ON public.ncaaf_team_game_stats (game_date);

COMMENT ON TABLE public.ncaaf_team_game_stats IS
  'Per-game team stats from CFBD, one row per (game, team). Immutable once '
  'final, so as-of-date aggregates can be rebuilt without leakage. Exists '
  'because a scores-only reconstruction could not beat the closing line — see '
  'project_pit_reconstruction_ceiling_930.';
COMMENT ON COLUMN public.ncaaf_team_game_stats.pregame_elo IS
  'CFBD Elo computed BEFORE kickoff — leak-free by construction.';
COMMENT ON COLUMN public.ncaaf_team_game_stats.off_ppa IS
  'CFBD calls it ppa; it is EPA per play. Offense = gained.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — read it back, do not trust the DDL succeeding.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT season, count(*) AS team_rows, count(DISTINCT cfbd_game_id) AS games,
--        min(game_date) AS first_game, max(game_date) AS last_game,
--        count(off_ppa) AS have_epa, count(pregame_elo) AS have_elo
--   FROM public.ncaaf_team_game_stats
--  GROUP BY season ORDER BY season;
