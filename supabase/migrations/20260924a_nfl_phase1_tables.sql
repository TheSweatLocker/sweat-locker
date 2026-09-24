-- NFL phase 1: the four tables that make the rest of the data answerable.
--
-- 2026-09-24. Andy: "lets know everything we can get out and the plan to
-- exploit for our processes." All 16 nflverse releases were probed and
-- resolve; player_stats already landed (38,504 -> 156,089 rows, no
-- migration needed). These four need tables.
--
-- Chosen for what they unblock, not for volume:
--
--   nfl_players         the canonical GSIS id map. Michael Penix Jr. was
--                       absent from our roster table on the eve of a game
--                       whose entire story was a quarterback change, and
--                       we had no way to notice. 24,830 rows fixes the
--                       class, not the instance.
--
--   nfl_rosters_weekly  who was ON the roster in a given week, 2002+.
--   nfl_injuries_weekly who was OUT in a given week.
--                       We hold both as CURRENT STATE only, which means
--                       "how does this team do without their WR1" cannot
--                       be asked about any past week. Competitors publish
--                       exactly that bullet ("last four games without
--                       Kyle Pitts"). Two small files close it.
--
--   nfl_snap_counts     role and usage. A receiver at 35% of snaps and one
--                       at 90% have different yardage distributions, and a
--                       trailing average cannot tell them apart. This is
--                       the most likely single improvement to the prop
--                       projection measured today.
--
-- WHY PROPS, SPECIFICALLY. Tested against all 1,287 graded NFL props we
-- have shipped, using a leak-guarded projection built only from games
-- whose entire week finished before the prop's date:
--
--       what we published   616-596   50.8%   (below the 52.4% breakeven)
--       projection side     706-506   58.3%
--
-- and nfl_pipeline_props.projection is populated on 0 of those 1,287. The
-- column already exists; nothing here is needed for it. These tables are
-- the inputs that should make that 58.3% better still.

-- ── canonical player identity ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nfl_players (
    gsis_id                text PRIMARY KEY,
    display_name           text NOT NULL,
    first_name             text,
    last_name              text,
    football_name          text,
    position               text,
    position_group         text,
    ngs_position           text,
    birth_date             date,
    height                 numeric,
    weight                 numeric,
    college_name           text,
    jersey_number          integer,
    rookie_season          integer,
    last_season            integer,
    latest_team            text,
    status                 text,
    years_of_experience    integer,
    draft_year             integer,
    draft_round            integer,
    draft_pick             integer,
    draft_team             text,
    -- cross-source ids: joining our own feeds is the recurring tax here
    esb_id                 text,
    nfl_id                 text,
    pfr_id                 text,
    pff_id                 text,
    espn_id                text,
    smart_id               text,
    headshot               text,
    updated_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS nfl_players_name_idx
    ON public.nfl_players (lower(display_name));
CREATE INDEX IF NOT EXISTS nfl_players_team_idx
    ON public.nfl_players (latest_team, position);

-- ── as-of-week roster ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nfl_rosters_weekly (
    season                 integer NOT NULL,
    week                   integer NOT NULL,
    game_type              text,
    team                   text NOT NULL,
    gsis_id                text,
    full_name              text,
    position               text,
    depth_chart_position   text,
    ngs_position           text,
    jersey_number          integer,
    status                 text,
    status_description_abbr text,
    years_exp              integer,
    entry_year             integer,
    rookie_year            integer,
    draft_club             text,
    draft_number           integer,
    height                 numeric,
    weight                 numeric,
    college                text,
    espn_id                text,
    pfr_id                 text,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    -- gsis_id is null on a handful of practice-squad rows, so the key
    -- falls back to the name rather than dropping those players.
    -- A PRIMARY KEY cannot hold an expression in Postgres, so the
    -- fallback is a generated column and the key is built on that.
    player_key             text GENERATED ALWAYS AS
                             (COALESCE(gsis_id, full_name)) STORED,
    PRIMARY KEY (season, week, team, player_key)
);
CREATE INDEX IF NOT EXISTS nfl_rosters_weekly_player_idx
    ON public.nfl_rosters_weekly (gsis_id, season, week);
CREATE INDEX IF NOT EXISTS nfl_rosters_weekly_lookup_idx
    ON public.nfl_rosters_weekly (season, week, team);

-- ── as-of-week injury report ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nfl_injuries_weekly (
    season                    integer NOT NULL,
    week                      integer NOT NULL,
    season_type               text,
    game_type                 text,
    team                      text NOT NULL,
    gsis_id                   text,
    full_name                 text,
    position                  text,
    report_primary_injury     text,
    report_secondary_injury   text,
    report_status             text,
    practice_primary_injury   text,
    practice_secondary_injury text,
    practice_status           text,
    updated_at                timestamptz NOT NULL DEFAULT now(),
    player_key                text GENERATED ALWAYS AS
                                (COALESCE(gsis_id, full_name)) STORED,
    PRIMARY KEY (season, week, team, player_key)
);
CREATE INDEX IF NOT EXISTS nfl_injuries_weekly_player_idx
    ON public.nfl_injuries_weekly (gsis_id, season, week);
-- "who was OUT that week" is the query this table exists for.
CREATE INDEX IF NOT EXISTS nfl_injuries_weekly_status_idx
    ON public.nfl_injuries_weekly (season, week, report_status);

-- ── snap counts ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nfl_snap_counts (
    game_id         text NOT NULL,
    season          integer NOT NULL,
    week            integer NOT NULL,
    game_type       text,
    team            text NOT NULL,
    opponent        text,
    player          text NOT NULL,
    pfr_player_id   text,
    position        text,
    offense_snaps   numeric,
    offense_pct     numeric,
    defense_snaps   numeric,
    defense_pct     numeric,
    st_snaps        numeric,
    st_pct          numeric,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- snap_counts carries no gsis_id, only a pfr id and a display name,
    -- so the key uses the pfr id where present. Joining these to
    -- nfl_player_stats runs through nfl_players.pfr_id — which is the
    -- reason nfl_players is in this same migration rather than later.
    player_key      text GENERATED ALWAYS AS
                      (COALESCE(pfr_player_id, player)) STORED,
    PRIMARY KEY (game_id, player_key)
);
CREATE INDEX IF NOT EXISTS nfl_snap_counts_player_idx
    ON public.nfl_snap_counts (pfr_player_id, season, week);
CREATE INDEX IF NOT EXISTS nfl_snap_counts_team_idx
    ON public.nfl_snap_counts (season, week, team);

COMMENT ON TABLE public.nfl_rosters_weekly IS
  'As-of-week roster. The point is the WEEK: current-state rosters cannot '
  'answer who was available in a past game, which is what every '
  '"without player X" claim depends on.';
COMMENT ON TABLE public.nfl_snap_counts IS
  'Per-game snap share. A trailing yardage average cannot distinguish a '
  'receiver on 35% of snaps from one on 90%; this is the input that can.';

NOTIFY pgrst, 'reload schema';
