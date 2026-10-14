-- panelist_predictions — one prediction per (sport, game, panelist, market)
--
-- 2026-10-14 · WHY
-- Andy's engine design has the models as a board of GameDay panelists: each
-- with their own data and their own equation, each MAKING A PICK, each
-- carrying its own RECORD, and the engine weighting those picks by those
-- records.
--
-- We did not have it. `model_picks`, `model_records`, `panelist_records`,
-- `model_performance`, `model_scorecard`, `voter_records` and `model_grades`
-- were all 404, and the panelists we did have were mostly the same number
-- again: NFL `model` writes `projected`'s own intermediates (100% side
-- agreement), NCAAF `sp_plus_pred_spread` is a literal alias of
-- `projected_spread`, NCAAF `mc` takes projected_spread/projected_total as
-- ARGUMENTS and simulates around them (99.6%), and NHL's three nameplates all
-- come out of one Elo predictor (100.0%). See panelist_registry.py for the
-- line-by-line provenance.
--
-- A new panelist therefore needs somewhere to live that is NOT a column bolted
-- onto one sport's context table. This is that place: one narrow table, every
-- sport, every panelist, every market. Adding a panelist becomes a row shape,
-- not a migration.
--
-- WHY `pregame` IS NOT OPTIONAL
-- A prediction written after the result is known is leakage, and a backfilled
-- prediction looks identical to a live one once it is in the table. So the
-- WRITER states which it is, explicitly, and the grader counts only pregame
-- rows toward a live record. Backfills stay queryable for backtesting and can
-- never be mistaken for a record. This is the same discipline that
-- feature_history's srs_fed/market_fed flags enforce — a derived number must
-- carry whether its inputs were actually there.

CREATE TABLE IF NOT EXISTS panelist_predictions (
    id              BIGSERIAL   PRIMARY KEY,

    sport           TEXT        NOT NULL,
    game_id         TEXT        NOT NULL,
    game_date       DATE        NOT NULL,

    -- The analyst. One name per equation, stable across seasons, e.g.
    -- 'rolling_form', 'drive_sim'. NOT a display label.
    panelist        TEXT        NOT NULL,

    -- 'margin' (predicted HOME margin) or 'total' (predicted combined score).
    -- Sign convention for margin is the one place it is stated: POSITIVE means
    -- the home team is predicted to win by that much, in EVERY sport. The
    -- close_spread sign mess (NFL away-line, everyone else home-line) stops at
    -- the writer's door.
    market          TEXT        NOT NULL,
    prediction      NUMERIC     NOT NULL,

    -- TRUE only when this was computed before the game was played. A live
    -- record may count pregame rows and nothing else.
    pregame         BOOLEAN     NOT NULL,

    -- What the equation actually read, so a prediction can never be silently
    -- re-based onto another model's output — which is how every duplicate in
    -- the current panel happened.
    inputs          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    model_version   TEXT,

    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT panelist_predictions_market_ck
        CHECK (market IN ('margin', 'total')),
    CONSTRAINT panelist_predictions_key
        UNIQUE (sport, game_id, panelist, market)
);

CREATE INDEX IF NOT EXISTS panelist_predictions_sport_date
    ON panelist_predictions (sport, game_date);
CREATE INDEX IF NOT EXISTS panelist_predictions_panelist
    ON panelist_predictions (sport, panelist, market);
CREATE INDEX IF NOT EXISTS panelist_predictions_pregame
    ON panelist_predictions (sport, panelist, pregame);

COMMENT ON TABLE panelist_predictions IS
    'One prediction per (sport, game, panelist, market). Each panelist is a '
    'distinct EQUATION over distinct INPUTS — see panelist_registry.py. '
    'market=margin is always a predicted HOME margin, positive = home wins by '
    'that much, in every sport. Only pregame=true rows may count toward a '
    'live record.';
COMMENT ON COLUMN panelist_predictions.pregame IS
    'Stated by the writer, never inferred. FALSE marks a backfill, which stays '
    'queryable for backtesting but must never be counted as a live record.';
COMMENT ON COLUMN panelist_predictions.inputs IS
    'What the equation read. Guards against a panelist being silently re-based '
    'onto another model''s output, which is how NCAAF mc ended up agreeing '
    'with projected 99.6% of the time.';

-- PostgREST will not see the new table until its schema cache is reloaded.
NOTIFY pgrst, 'reload schema';
