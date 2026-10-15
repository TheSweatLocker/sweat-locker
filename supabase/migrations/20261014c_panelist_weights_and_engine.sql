-- panelist_weights + engine_shadow_picks — the weighting layer the engine needs
--
-- 2026-10-14 · WHY
-- ANDY, on the engine: "the models for me represent if you're watching College
-- Football GameDay on ESPN and you have a board of five panelists talking and
-- they make their picks for a given game. They have their own data and
-- calculations and takes." The engine then weighs those picks BY THEIR RECORDS,
-- alongside money flow, situation, tendencies, rolling stats, matchup,
-- injuries, situational records, regression factors and the market.
--
-- We had the picks (build_panelist_records.py) and the records, and NOTHING
-- that turned a record into a weight. Measured 2026-10-14: signal_sources
-- carries a `weight_registry_key` column -- the hook for exactly this -- and
-- it is populated on 75 of 842 rows, while the registry it points at DOES NOT
-- EXIST (signal_weights, weight_registry, rule_weights, signal_weight_registry
-- all 404). 652 of 842 signals have no measured record at all.
--
-- ANDY: "we wont know until we build and monitor surely its better than what we
-- are doing." Correct, and the argument I gave against it was weaker than his:
-- the engine ALREADY weights its panelists -- equally -- and that equal weight
-- includes NFL v4 at -9.6% ROI and duplicate panelists that inflate agreement.
-- Record-aware weighting is strictly better than uniform weighting of a panel
-- known to contain a constant and several copies. And a weighting scheme that
-- is never run, not even in shadow, can never accumulate the record that would
-- justify it.
--
-- So: build it, run it in shadow, monitor it, promote it on evidence.

-- ═══════════════════════════════════════════════════════════════════════
-- panelist_weights — one weight per (sport, panelist, market)
-- ═══════════════════════════════════════════════════════════════════════
-- Recomputed from records on a cadence. Every row records WHAT RECORD it was
-- derived from, because a weight whose basis cannot be reconstructed is a
-- magic number.
CREATE TABLE IF NOT EXISTS panelist_weights (
    id              BIGSERIAL   PRIMARY KEY,

    sport           TEXT        NOT NULL,
    panelist        TEXT        NOT NULL,
    market          TEXT        NOT NULL,

    -- The multiplier applied to this panelist's vote. 1.0 = the uniform
    -- weight the engine applies today, so a weight of 1.0 everywhere
    -- reproduces current behaviour exactly and the change is auditable.
    -- 0.0 = does not vote (a duplicate, an alias, or an unseatable panelist).
    weight          NUMERIC     NOT NULL DEFAULT 1.0,

    -- Why it is what it is.
    basis           TEXT        NOT NULL,   -- 'record' | 'registry_block' | 'no_record'
    n               INTEGER,                -- decisions the record rests on
    hit_rate        NUMERIC,
    roi             NUMERIC,
    -- The record's edge AFTER shrinkage toward breakeven. The raw edge on
    -- n=60 is mostly noise; this is what the weight actually uses.
    shrunk_edge     NUMERIC,
    -- TRUE when derived from pregame=true rows (a live record) rather than a
    -- walk-forward backfill. Backfills are valid for sizing a prior; they are
    -- not a live record, and the distinction must survive into the weight.
    from_live       BOOLEAN     NOT NULL DEFAULT FALSE,

    model_version   TEXT,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT panelist_weights_market_ck
        CHECK (market IN ('margin', 'total')),
    CONSTRAINT panelist_weights_range_ck
        CHECK (weight >= 0 AND weight <= 3),
    CONSTRAINT panelist_weights_key
        UNIQUE (sport, panelist, market)
);

CREATE INDEX IF NOT EXISTS panelist_weights_sport
    ON panelist_weights (sport, market);

COMMENT ON TABLE panelist_weights IS
    'Engine weight per (sport, panelist, market), derived from that panelist''s '
    'graded record with shrinkage toward breakeven. weight=1.0 reproduces the '
    'uniform weighting the engine applies today; weight=0 means the panelist '
    'does not vote (duplicate/alias/unseatable per panelist_registry.py).';
COMMENT ON COLUMN panelist_weights.shrunk_edge IS
    'Record edge shrunk toward breakeven by n/(n+n0). A raw edge on n=60 is '
    'mostly noise; the weight uses this, never the raw hit rate.';
COMMENT ON COLUMN panelist_weights.from_live IS
    'TRUE only when built from pregame=true predictions. A walk-forward '
    'backfill may size a prior but is not a live record.';

-- ═══════════════════════════════════════════════════════════════════════
-- engine_shadow_picks — what the weighted panel WOULD have picked
-- ═══════════════════════════════════════════════════════════════════════
-- Writes no published pick. It exists so the weighted engine accumulates a
-- forward record that can be compared, head to head, against what actually
-- shipped on the same games. Promotion is then an evidence decision instead of
-- a preference.
CREATE TABLE IF NOT EXISTS engine_shadow_picks (
    id              BIGSERIAL   PRIMARY KEY,

    sport           TEXT        NOT NULL,
    game_id         TEXT        NOT NULL,
    game_date       DATE        NOT NULL,
    market          TEXT        NOT NULL,

    -- 'HOME' | 'AWAY' for margin, 'OVER' | 'UNDER' for total, 'PASS' when the
    -- weighted panel does not clear its threshold. PASS is a real decision and
    -- is stored, so the engine's discipline is measurable and not just its
    -- hit rate on the games it chose to play.
    side            TEXT        NOT NULL,

    -- The weighted net across panelists, and how much panel there was.
    net             NUMERIC     NOT NULL,
    total_weight    NUMERIC     NOT NULL,
    voters          INTEGER     NOT NULL,
    -- Per-panelist detail: who voted which way, at what weight, off what
    -- number. Without this a shadow pick cannot be explained after the fact.
    detail          JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- The market it was measured against, frozen at write time.
    market_line     NUMERIC,
    -- The existing engine's own confluence net on the same game, so the two
    -- can be compared rather than the new one judged in isolation.
    confluence_net  NUMERIC,

    pregame         BOOLEAN     NOT NULL,
    engine_version  TEXT,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT engine_shadow_picks_market_ck
        CHECK (market IN ('margin', 'total')),
    CONSTRAINT engine_shadow_picks_side_ck
        CHECK (side IN ('HOME', 'AWAY', 'OVER', 'UNDER', 'PASS')),
    CONSTRAINT engine_shadow_picks_key
        UNIQUE (sport, game_id, market, engine_version)
);

CREATE INDEX IF NOT EXISTS engine_shadow_picks_sport_date
    ON engine_shadow_picks (sport, game_date);
CREATE INDEX IF NOT EXISTS engine_shadow_picks_pregame
    ON engine_shadow_picks (sport, pregame, market);

COMMENT ON TABLE engine_shadow_picks IS
    'What the record-weighted panel WOULD have picked. Publishes nothing. '
    'Exists so the weighted engine builds a forward record comparable, game '
    'for game, against what actually shipped — so promotion is an evidence '
    'decision. PASS rows are stored deliberately: discipline is measurable.';
COMMENT ON COLUMN engine_shadow_picks.detail IS
    'Per-panelist votes, weights and the number each voted off. A shadow pick '
    'that cannot be explained after the fact cannot be promoted.';

-- PostgREST will not see the new tables until its schema cache is reloaded.
NOTIFY pgrst, 'reload schema';
