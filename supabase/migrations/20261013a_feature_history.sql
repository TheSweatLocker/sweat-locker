-- ════════════════════════════════════════════════════════════════════
-- feature_history — point-in-time team features, one row per game
-- 2026-10-13
--
-- WHY THIS TABLE EXISTS
-- Three separate modelling attempts died on the same constraint on 10-12/13:
--
--   SOR as a signal        stored SOR columns start 2026-10-10
--                          -> 14 NFL / 45 NCAAF graded rows
--   football LR retrain    NFL has 79 past context rows vs a 300 minimum
--   NCAAF cover model      203 training rows against 17 features
--                          -> +12.1pp overfit
--
-- The cause is that `*_game_context` holds only the CURRENT season and is
-- written forward (316 NFL rows, 536 NCAAF). The results tables hold years
-- -- NFL 7,355 played games back to 1999, NCAAF 15,535 back to 2022 -- but
-- carry only scores and market data. So every team FEATURE we own exists for
-- about six weeks of history, and nothing that needs team features can be
-- validated.
--
-- This table is the reconstruction: for every game, the feature vector as it
-- would have been known BEFORE that game, computed from games strictly
-- earlier. Written once per game and then immutable -- a point-in-time row
-- that gets recomputed later is not point-in-time.
--
-- WHAT IS DELIBERATELY ABSENT
-- Anything sourced from a third-party SNAPSHOT cannot be rebuilt as-of a past
-- date and is excluded rather than imputed: SP+, talent composite, returning
-- production, Madden ratings, EPA, success rate, explosiveness, OL/DL weight.
-- Imputing those is how the NCAAF cover model handed Mercyhurst an average
-- FBS SP+ and learned that a 33-point cupcake is a normal game.
--
-- WHY `*_fed` FLAGS INSTEAD OF IMPUTED ZEROS
-- project_prop_projection_slope_zero_1012: the prop projection measured
-- +0.103 CI[+0.029,+0.176] on the 2,847 rows where its inputs existed and
-- -1.247 on the 237 where they did not. Pooled, those averaged to a
-- meaningless +0.030. A row with two games of history is not a row with a
-- small rating -- it is a row with NO rating, and callers must be able to
-- filter on that rather than infer it from a value near zero.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS feature_history (
    id              BIGSERIAL PRIMARY KEY,
    sport           TEXT        NOT NULL,
    game_id         TEXT        NOT NULL,
    game_date       DATE        NOT NULL,
    season          TEXT,
    home_team       TEXT        NOT NULL,
    away_team       TEXT        NOT NULL,

    -- Headline scalars get real columns so they can be indexed, filtered and
    -- eyeballed in SQL. The complete vector also lives in `features`.
    home_srs        REAL,
    away_srs        REAL,
    srs_diff        REAL,
    home_sos        REAL,
    away_sos        REAL,
    sos_diff        REAL,
    home_resid_l5   REAL,
    away_resid_l5   REAL,

    -- How much history each side's features were computed from. The honest
    -- gate for every downstream consumer.
    home_games_used INT         NOT NULL DEFAULT 0,
    away_games_used INT         NOT NULL DEFAULT 0,
    srs_fed         BOOLEAN     NOT NULL DEFAULT FALSE,
    market_fed      BOOLEAN     NOT NULL DEFAULT FALSE,

    -- The full point-in-time vector. JSONB rather than 60 columns because the
    -- feature set will change as reconstruction improves, and a migration per
    -- feature is how a table like this stops being maintained.
    features        JSONB       NOT NULL DEFAULT '{}'::jsonb,

    built_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT feature_history_sport_game UNIQUE (sport, game_id)
);

CREATE INDEX IF NOT EXISTS feature_history_sport_date
    ON feature_history (sport, game_date);
CREATE INDEX IF NOT EXISTS feature_history_sport_season
    ON feature_history (sport, season);
-- Trainers select on the fed flags before anything else.
CREATE INDEX IF NOT EXISTS feature_history_fed
    ON feature_history (sport, srs_fed, market_fed);

COMMENT ON TABLE feature_history IS
    'Point-in-time team features per game, computed from strictly-earlier '
    'games only. Immutable once written. Built by build_feature_history.py. '
    'Validate against the live *_game_context columns on the overlap window '
    'BEFORE training anything on it (step 3 of the project).';
COMMENT ON COLUMN feature_history.srs_fed IS
    'Both teams had >= MIN_GAMES_SRS prior games this season. FALSE means the '
    'rating columns are not trustworthy -- filter, do not impute.';
COMMENT ON COLUMN feature_history.market_fed IS
    'Both teams had >= MIN_GAMES_MARKET prior games WITH a closing line, so '
    'the ATS/OU/residual features are real. NCAAF historical market coverage '
    'is only 38-42%, so this is FALSE on most pre-2026 college rows.';

NOTIFY pgrst, 'reload schema';
