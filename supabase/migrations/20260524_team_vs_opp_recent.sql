-- mlb_team_vs_opp_recent: head-to-head rolling stats per team-opponent pair.
--
-- Built 2026-05-24 after the LAA-vs-TEX gap: LAA L14 wRC+ proxy showed
-- their overall offense as ICE COLD (51 vs season 99), but they had put
-- 14 runs on Texas in 2 H2H games. Team-level recency missed the matchup-
-- specific pattern.
--
-- This table stores per-(team, opponent) aggregates over the last N H2H
-- games this season. Read by game_context confluence as an OVERRIDE: when
-- opp-specific recency contradicts the team-level signal, opp-specific
-- wins (with a recency-weighted confidence).

create table if not exists mlb_team_vs_opp_recent (
    id                  bigserial primary key,
    team                text        not null,
    opponent            text        not null,
    season              integer     not null,
    -- Aggregate over the LAST 5 H2H games this season (rolling)
    games_played        integer     not null,
    runs_scored         integer     not null,
    runs_allowed        integer     not null,
    hits                integer,
    at_bats             integer,
    walks               integer,
    hbp                 integer,
    sac_flies           integer,
    ops                 numeric(5,3),
    -- Most recent H2H game date — used to decay confidence when stale
    last_h2h_date       date,
    -- Avg runs per game vs THIS opponent
    rpg_vs_opp          numeric(5,2),
    -- Delta: rpg_vs_opp - team's overall L14 rpg (positive = HOT vs this opp)
    rpg_delta_vs_l14    numeric(5,2),
    computed_date       date        not null,
    updated_at          timestamptz default now(),
    unique (team, opponent, season)
);

create index if not exists mlb_team_vs_opp_recent_team_idx
  on mlb_team_vs_opp_recent (team, season);

create index if not exists mlb_team_vs_opp_recent_pair_idx
  on mlb_team_vs_opp_recent (team, opponent, season);

comment on column mlb_team_vs_opp_recent.rpg_vs_opp
  is 'Runs/game scored vs this specific opponent over the last 5 H2H games. Captures matchup-specific hot/cold patterns that team-level L14 misses.';
comment on column mlb_team_vs_opp_recent.rpg_delta_vs_l14
  is 'rpg_vs_opp minus team overall L14 rpg. Positive = HOT vs this opponent specifically. Used as confluence override when |delta| >= 1.0 R/G.';

-- Force PostgREST schema cache reload — without this, PGRST204 errors
-- on writes for up to 10 minutes while cache catches up.
NOTIFY pgrst, 'reload schema';
