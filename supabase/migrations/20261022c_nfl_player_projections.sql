-- == 2026-10-22 · NFL ENGINE PLAYER PROJECTIONS, PERSISTED ==
--
-- ── WHY "ENGINE" IS IN THE NAME ─────────────────────────────────────────
-- The obvious name, nfl_player_projections, IS ALREADY TAKEN, and the first
-- version of this migration collided with it: CREATE TABLE IF NOT EXISTS
-- silently no-opped against the existing table, and then the unique index
-- failed with "column game_id does not exist" - the index was the only
-- statement that actually touched the schema.
--
-- That table is a live THIRD-PARTY FANTASY feed, not ours: 15,356 rows,
-- 2026 week 8, sources `espn_fantasy` (456) and `sleeper` (544), pulled
-- 2026-10-22T21:25. It is keyed by (source, season, week, player) with
-- fantasy columns - proj_fantasy_pts, proj_fg_made, proj_def_sacks - and has
-- no game_id at all, because a weekly fantasy projection is not attached to
-- a matchup.
--
-- This table is a different thing and must not be confused with it: OUR
-- engine'"'"'s per-GAME projection, keyed by game_id, carrying the multiplier
-- chain that produced it. Keeping both is deliberate - a vendor projection
-- beside ours is the benchmark that tells us whether our number is worth
-- anything, the same reason sor_benchmark sits beside our SOR instead of
-- replacing it.
--
-- ANDY: "in nfl game detail displaying stat projections for starting QBs like
-- we [do for] starting pitcher in mlb ... what our engine assess stat line to
-- be projected at based on opponent defensive stats, performances at
-- home/away, the whole nine yards."
--
-- MLB already works this way: mlb_game_context carries
-- home/away_pitcher_projected_ks/bb/er/hits/outs and the Panel turns player
-- projections into an implied total and margin. NFL had nothing equivalent
-- persisted, and NBA has nothing at all.
--
-- ── WHY A TABLE AND NOT COLUMNS ON nfl_game_context ─────────────────────
-- MLB gets away with columns because a baseball game has exactly two
-- projected players — the starting pitchers. A football game has a QB, two
-- or three RBs, three or four WRs and a TE per side. Twelve columns per
-- stat per side does not survive the first time we add a receiver, and the
-- Andy framework explicitly ends at "work that back to anytime touchdown
-- and ... eventually points which will lead to predicted score", which means
-- every skill position eventually lands here.
--
-- One row per (game, player) keeps the shape stable as positions are added
-- and lets the Panel aggregate by summing rows rather than by knowing which
-- columns exist.
--
-- ── WHY NOT REUSE project_nfl_stats.py ──────────────────────────────────
-- Because it is two seasons stale and cannot be fixed in place. It sources
-- nfl_data_py, and nfl_data_py 404s for BOTH 2025 and 2026 — the package's
-- upstream only carries through 2024. Measured today, its "last 5 games" for
-- Caleb Williams were weeks 14-18 of 2024, and its opponent-defence baseline
-- used `_completed_seasons(2026) -> [2024, 2023]` by a hardcoded year-2 rule
-- written in August.
--
-- Concretely it had Drake Maye at 163.6 passing yards off a 2024 L5. His
-- actual 2026 L5 is 208 / 199 / 269 / 374 / 235 — about 257. A ~94-yard
-- error, on a number we were about to put on a game card.
--
-- It reaches no users today (its only caller is _legacy/generate_nfl_props.py
-- and it appears in zero workflows), so this is a dead module, not a live
-- defect. The replacement sources our own tables, which ARE current:
-- nfl_player_stats holds 2026 weeks 1-6, nfl_positional_defense is all-2026
-- and refreshed nightly.
--
-- ── NOT A BETTING SURFACE YET ───────────────────────────────────────────
-- `model_version` and the full `inputs` blob exist so these can be GRADED
-- against actuals before any of it is allowed to move a tier or a price.
-- Until that grading exists this is an explanation of our number, not a
-- claim of edge — the same discipline that keeps a slope from becoming a
-- betting rule.

CREATE TABLE IF NOT EXISTS public.nfl_engine_player_projections (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    game_id         TEXT        NOT NULL,
    game_date       DATE        NOT NULL,
    season          INT         NOT NULL,
    week            INT,
    player_name     TEXT        NOT NULL,
    player_id       TEXT,
    team            TEXT        NOT NULL,
    opp_team        TEXT        NOT NULL,
    position        TEXT        NOT NULL,
    home_away       TEXT        NOT NULL CHECK (home_away IN ('HOME', 'AWAY')),

    -- Passing
    proj_pass_yds   NUMERIC(6,1),
    proj_pass_tds   NUMERIC(4,2),
    proj_pass_att   NUMERIC(5,1),
    proj_pass_cmp   NUMERIC(5,1),
    proj_ints       NUMERIC(4,2),
    -- Rushing / receiving, so RB and WR/TE slot in without a migration
    proj_rush_yds   NUMERIC(6,1),
    proj_rush_tds   NUMERIC(4,2),
    proj_rec_yds    NUMERIC(6,1),
    proj_rec        NUMERIC(5,1),
    proj_rec_tds    NUMERIC(4,2),
    proj_any_td     NUMERIC(4,3),

    --: Every multiplier that produced the numbers above, named, so the card
    --: can show the reasoning and an audit can reproduce it. This is the
    --: "no hallucinations" contract: a projection that cannot explain itself
    --: does not get displayed.
    inputs          JSONB,
    model_version   TEXT        NOT NULL DEFAULT 'nfl_player_proj_v1',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

--: One projection per player per game. The writer upserts on this, so a
--: re-run after a line move or an injury update REPLACES rather than
--: accumulating vintages - the `data[0]` unordered-vintage trap.
CREATE UNIQUE INDEX IF NOT EXISTS nfl_engine_player_proj_game_player_idx
    ON public.nfl_engine_player_projections (game_id, player_name, position);

--: The card reads by game, the grader reads by date.
CREATE INDEX IF NOT EXISTS nfl_engine_player_proj_date_idx
    ON public.nfl_engine_player_projections (game_date);

NOTIFY pgrst, 'reload schema';
