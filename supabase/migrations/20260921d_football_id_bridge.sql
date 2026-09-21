-- 20260921d — bridge the football game_id schemes
--
-- THE DEFECT. The football pipelines use several different game_id schemes
-- and nothing connects them:
--
--   nfl_game_context      000fc688beb4fc004ecdad115d9adb1c   (hash)
--   nfl_game_results      2020_01_ARI_SF                     (season_week_away_home)
--   ncaaf_game_context    ncaaf_20260829_Hawaii_Stanford     (slug)
--   ncaaf_game_results    cfbd_401403853 AND ncaaf_2026...   (MIXED)
--   line_movement_flags   whichever scheme was current when written
--
-- Measured 2026-09-21: 0 of 316 nfl_game_context ids appear in
-- nfl_game_results. Anything keyed on a context id cannot be graded, ever.
--
-- WHAT IT COST. line_movement_flags stores the context id — correctly, since
-- at write time the game has not been played and no result row exists. So of
-- 492 football sharp-money flags, NFL graded 0/51 and NCAAF 25/441. The whole
-- money-flow classification (SHARP_MOVE / PUBLIC_MOVE / RLM / CONSENSUS) has
-- never been measured on football. Its only validation is MLB, where the
-- SHARP_MOVE family shows no edge at all
-- (project_fade_gate_performance_921). For the two sports Andy wants "state
-- of the art", the gate is currently unfalsifiable.
--
-- WHY AN ALIAS TABLE rather than one column on results. A game accumulates
-- MORE THAN ONE historical id: NCAAF switched from hash to slug ids around
-- 2026-09-19, so 411 legacy flags carry ids that exist in neither context nor
-- results — they survive only in line_history, which keeps `matchup` and
-- `commence_time` and so can still be resolved. A single context_game_id
-- column is many-to-one the wrong way round and could not hold both. This
-- table takes any id ever seen and points it at the canonical results id, so
-- old rows stay gradeable and the next scheme change costs one more INSERT
-- rather than another migration.
--
-- Populated by bridge_football_game_ids.py, which matches on
-- (game_date, away_team, home_team) through the curated alias tables and
-- requires a UNIQUE hit — college football is exactly where fuzzy matching
-- welds 'Iowa' onto 'Northern Iowa'.

CREATE TABLE IF NOT EXISTS football_game_id_alias (
    sport           TEXT NOT NULL,          -- 'NFL' | 'NCAAF'
    alias_game_id   TEXT NOT NULL,          -- any id seen in flags/context/line_history
    results_game_id TEXT NOT NULL,          -- canonical id in <sport>_game_results
    game_date       DATE,
    away_team       TEXT,
    home_team       TEXT,
    source          TEXT,                   -- 'context' | 'line_history'
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (sport, alias_game_id)
);

COMMENT ON TABLE football_game_id_alias IS
    'Maps any historical football game_id (context hash, legacy hash, slug) to '
    'the canonical <sport>_game_results id. Join key for anything written '
    'against a context id — line_movement_flags, receipts, the pattern miner.';

CREATE INDEX IF NOT EXISTS fgia_results_gid
    ON football_game_id_alias (sport, results_game_id);
CREATE INDEX IF NOT EXISTS fgia_date
    ON football_game_id_alias (sport, game_date DESC);

-- Pipeline plumbing, not a user surface. Same posture as the signal tables;
-- the 2026-09-09 audit closed 4 tables that were open by default.
ALTER TABLE football_game_id_alias ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
