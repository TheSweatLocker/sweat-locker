-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-01 · NFL positional defensive vulnerability
--
-- WHY
-- ---
-- Andy: "I want to ensure signal coverage includes defense performance against
-- that position, does the secondary give up tds, does the defense give up a lot
-- of rushing tds, qb or rb? That should be thinking of the prop engine."
--
-- It could not. nfl_team_defense_stats holds SIX team-level numbers --
-- def_pass_epa_allowed, def_pass_ypg, def_ppg, def_rush_epa_allowed,
-- def_rush_ypg, def_ypg -- with no positional split and no TDs allowed at all.
-- So the prop engine's entire opponent input is a single EPA figure applied as
-- a +/-7.5% multiplier (nfl_generate_props.project: base *= 1.0 +
-- (opp_rank_pct - 0.5) * 0.15), and player_anytime_td borrows pass-EPA as a
-- proxy for red-zone vulnerability.
--
-- THE SPREADS DWARF THAT ADJUSTMENT. Derived from nfl_player_stats 2026 wk1-3:
--   rushing TDs allowed to RB   GB 2.00/g  ...  HOU/CLE/ATL 0.00/g   (2.00 spread)
--   rushing TDs allowed to QB   PHI 1.00/g ...  BUF/ATL/ARI 0.00/g   (1.00)
--   rushing yards to RB         CAR 144.7/g ... ATL 48.0/g           (96.7)
--   receiving yards to WR       ATL 201.3/g ... SEA 95.0/g          (106.3)
-- A model that can move a projection by at most 7.5% cannot express a 97-yard
-- or 2-TD-per-game difference between opponents.
--
-- NO NEW FEED NEEDED. nfl_player_stats already carries `opponent_team` beside
-- `position`, rushing_tds, receiving_tds, rushing_yards, receiving_yards,
-- targets and carries -- per player, per week -- so "what does this defense
-- allow to this position" is a pure aggregation of data we hold.
--
-- SAMPLE IS THIN AND THAT IS HANDLED, NOT IGNORED. 2026 has only weeks 1-3
-- loaded (three games per team), so GB's 2.00 rushing TD/g is six TDs in three
-- games and will regress hard. Every rate therefore ships in two forms: the raw
-- current-season rate, and a *_blended rate shrunk toward the prior season (or
-- the league mean for that position when a team has no prior rows) by games
-- played. Consumers should read the blended column; the raw one exists so the
-- shrinkage can be audited rather than trusted.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS public.nfl_positional_defense (
  season            integer     NOT NULL,
  season_type       text        NOT NULL DEFAULT 'REG',
  team              text        NOT NULL,          -- the DEFENSE
  position          text        NOT NULL,          -- QB / RB / WR / TE
  games             integer,                       -- games this defense played

  -- raw current-season rates allowed to this position, per game
  rush_td_pg        numeric,
  rec_td_pg         numeric,
  any_td_pg         numeric,                       -- rush + rec
  rush_yds_pg       numeric,
  rec_yds_pg        numeric,
  carries_pg        numeric,
  targets_pg        numeric,
  receptions_pg     numeric,

  -- shrunk toward prior season / league mean by games played. READ THESE.
  rush_td_pg_blended   numeric,
  rec_td_pg_blended    numeric,
  any_td_pg_blended    numeric,
  rush_yds_pg_blended  numeric,
  rec_yds_pg_blended   numeric,

  -- provenance so a number can be explained in the app and in a read
  prior_games       integer,
  blend_label       text,
  league_mean_any_td numeric,

  -- ranks over the blended rates; 1 = allows the MOST (most vulnerable)
  rank_any_td       integer,
  rank_rush_yds     integer,
  rank_rec_yds      integer,
  league_size       integer,

  -- explicit, never DEFAULT now(): these are upserts, and a DEFAULT only
  -- fires on INSERT, which is how SOS/SOR sat four days stale with fresh
  -- values and nobody could tell (project_sos_sor_root_cause_930).
  refreshed_at      timestamptz,

  PRIMARY KEY (season, season_type, team, position)
);

CREATE INDEX IF NOT EXISTS idx_nfl_posdef_lookup
  ON public.nfl_positional_defense (season, team, position);

COMMENT ON TABLE public.nfl_positional_defense IS
  'What each NFL defense allows to each position, per game. Derived from '
  'nfl_player_stats grouped by opponent_team + position — no external feed. '
  'Read the *_blended columns; raw rates are unshrunk and noisy early season.';
COMMENT ON COLUMN public.nfl_positional_defense.rank_any_td IS
  'Rank over any_td_pg_blended, 1 = allows the MOST TDs to this position '
  '(i.e. most vulnerable / best to target).';
COMMENT ON COLUMN public.nfl_positional_defense.blend_label IS
  'Human-readable provenance, e.g. "blended · 3 games this season + 2025 '
  'season" — mirrors nfl_game_context._nfl_blend_label.';

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — read it back, do not trust the DDL succeeding.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT position, count(*) AS teams,
--        round(min(any_td_pg_blended),2) AS min_any_td,
--        round(max(any_td_pg_blended),2) AS max_any_td,
--        round(max(rush_yds_pg_blended),1) AS max_rush_yds,
--        max(games) AS games, max(refreshed_at) AS refreshed
--   FROM public.nfl_positional_defense
--  WHERE season = 2026 GROUP BY position ORDER BY position;
