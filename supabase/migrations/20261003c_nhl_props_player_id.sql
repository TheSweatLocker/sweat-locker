-- ════════════════════════════════════════════════════════════════════════
-- 2026-10-03 · NHL PROP IDENTITY — player_id
--
-- WHY
-- ---
-- Prerequisite 1 of the NHL/NBA prop scoring layer. Andy's vision for Prop
-- Jerry is "surface props that have a matchup edge based on performance
-- history, patterns from our data, and overall is it a good spot" -- not a
-- flood of COVERAGE rows. The matchup half of that needs a join from a prop
-- to the player's history against THIS opponent, and that join does not
-- currently exist in any form.
--
-- nhl_player_vs_team already holds the data: 24,450 rows, 1,094 players,
-- 8,080 of them with 3+ career games vs a given opponent, carrying
-- career_shots_avg / goals / assists / points / toi / pp_pts and sv_pct.
-- It keys on player_id.
--
-- nhl_pipeline_props has no player_id, and the two name formats do not
-- match: nhl_player_vs_team stores "Z. Benson" while the props carry the
-- book's full name "Zach Benson". Measured on the 2026-10-03 board: 0 of
-- 497 prop players join. Zero. The matchup layer is unreachable.
--
-- WHY A COLUMN AND NOT A NAME TRANSFORM
-- -------------------------------------
-- Deriving "Z. Benson" from "Zach Benson" matches 448 of 497 (90%), which
-- is good but not free: 10 of the 1,094 names in nhl_player_vs_team already
-- collapse to the same initial+surname (A. Lee, J. Slavin, C. Smith,
-- J. Anderson, D. Hunt, L. Carlsson...), so a name join silently attaches a
-- prop to the wrong athlete roughly 1% of the time. That is the exact
-- failure that put Andrei Kopylov's 6-11 record on Roman Kopylov's UFC card
-- (project_stat_integrity_audit_1002), and it is invisible on read.
--
-- api-web.nhle.com gives the real id for free. nhl_player_log.resolve_player
-- already builds an exact name index from all 32 club rosters and returns
-- (player_id, team_abbrev, position) -- one roster call per club, cached.
-- So the id is available without a new fetch path, and an exact join is
-- permanently correct rather than 90% correct.
--
-- text, not bigint: nhl_player_vs_team.player_id is text ('8484145'), and a
-- type mismatch across the join is its own silent-zero-rows bug.
--
-- team_abbrev / opp_abbrev / player_position already EXIST on the table and
-- are NULL on all 13,174 rows -- no migration needed for those, they just
-- need populating. enrich_nhl_prop_identity.py fills all four.
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.nhl_pipeline_props
  ADD COLUMN IF NOT EXISTS player_id text;

COMMENT ON COLUMN public.nhl_pipeline_props.player_id IS
  'NHL api-web player id as TEXT, matching nhl_player_vs_team.player_id so '
  'the matchup-history join is exact rather than name-based. Written by '
  'enrich_nhl_prop_identity.py via nhl_player_log.resolve_player. NULL means '
  'the book name could not be resolved against any club roster -- treat as '
  '"no matchup history available", never fall back to a name match.';

-- The scoring layer reads props for one date and joins per player, so this
-- is the access path. Partial: a NULL player_id is never joined on.
CREATE INDEX IF NOT EXISTS nhl_pipeline_props_player_id_idx
  ON public.nhl_pipeline_props (player_id)
  WHERE player_id IS NOT NULL;

-- Date + player is how the enrichment pass and the scorer both read.
CREATE INDEX IF NOT EXISTS nhl_pipeline_props_date_player_idx
  ON public.nhl_pipeline_props (game_date, player_name);

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════
-- VERIFY — count it back. A successful ALTER is not proof PostgREST can see
-- the column, and this table has already produced a 0-row join once today.
-- ════════════════════════════════════════════════════════════════════════
-- SELECT game_date,
--        count(*)                        AS props,
--        count(player_id)                AS have_player_id,
--        count(team_abbrev)              AS have_team,
--        count(opp_abbrev)               AS have_opp,
--        count(player_position)          AS have_pos
--   FROM public.nhl_pipeline_props
--  GROUP BY game_date
--  ORDER BY game_date DESC;
--
-- Join reachability — this is the number that was 0 of 497:
-- SELECT count(DISTINCT p.player_id) AS joinable_players
--   FROM public.nhl_pipeline_props p
--   JOIN public.nhl_player_vs_team v ON v.player_id = p.player_id
--  WHERE p.game_date = CURRENT_DATE;
