-- 2026-08-18 (late): two tables for the Ledger v3 build.
--
-- 1) alt_line_snapshots — cached alternate market lines (Over 6.5, +2.5,
--    etc.) pulled ONCE per day from Odds API. Ledger reads real prices
--    instead of the teaser_price() estimator. Cache keeps API cost low
--    (~30 credits/day across sports vs 90/day if we fetched per cron).
--
-- 2) ledger_snapshots — immutable record of every shipped Ledger
--    suggestion at generation time. Grader backfills W/L + unit_pnl
--    against the LOCKED odds so historical record reflects what the
--    user actually saw when they placed the combo. Mirrors the
--    prop_pick_snapshots pattern from earlier today.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════
-- 1. ALT LINE SNAPSHOTS
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS alt_line_snapshots (
  id BIGSERIAL PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  sport TEXT NOT NULL,
  game_id TEXT NOT NULL,             -- Odds API game id (matches game_context.game_id)
  book TEXT NOT NULL DEFAULT 'draftkings',
  market TEXT NOT NULL,              -- 'alternate_totals' | 'alternate_spreads'
  side TEXT NOT NULL,                -- 'OVER' | 'UNDER' | 'HOME' | 'AWAY'
  line NUMERIC NOT NULL,             -- 6.5, -2.5, etc.
  price INT NOT NULL,                -- American odds
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS alt_line_snapshots_uniq
  ON alt_line_snapshots (snapshot_date, sport, game_id, book, market, side, line);

CREATE INDEX IF NOT EXISTS alt_line_snapshots_lookup
  ON alt_line_snapshots (game_id, market, snapshot_date);

ALTER TABLE alt_line_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS alt_line_snapshots_public_read ON alt_line_snapshots;
CREATE POLICY alt_line_snapshots_public_read
  ON alt_line_snapshots FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS alt_line_snapshots_service_role_write ON alt_line_snapshots;
CREATE POLICY alt_line_snapshots_service_role_write
  ON alt_line_snapshots FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE alt_line_snapshots IS
  'Cached alt total + alt spread lines per game, fetched ~1x/day from Odds API. Ledger reads real prices at 6.5/7.5/+2.5 etc. instead of teaser_price() estimator.';

-- ═══════════════════════════════════════════════════════════════════════
-- 2. LEDGER SNAPSHOTS (transparency + accountability)
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ledger_snapshots (
  id BIGSERIAL PRIMARY KEY,
  game_date DATE NOT NULL,
  snapshotted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ledger_suggestion_id BIGINT,   -- FK to ledger_suggestions.id (nullable — soft link)
  kind TEXT NOT NULL,            -- 'chalk_parlay' | 'teased_totals_combo' | 'teased_spreads_combo' | 'teaser'
  sport_scope TEXT NOT NULL,     -- 'MLB' / 'MULTI' / etc.
  legs JSONB NOT NULL,           -- immutable snapshot of legs at lock time (side/line/price/tier)
  combined_odds INT NOT NULL,
  reasoning TEXT,
  -- Grading (backfilled by grade_ledger_snapshots)
  result TEXT,                   -- 'Win' | 'Loss' | 'Push' | 'Void'
  legs_hit INT,                  -- how many legs cashed
  legs_pushed INT,
  graded_at TIMESTAMPTZ,
  unit_pnl NUMERIC               -- realized PnL at 1u stake per combo
);

CREATE UNIQUE INDEX IF NOT EXISTS ledger_snapshots_uniq
  ON ledger_snapshots (game_date, kind, sport_scope, combined_odds);
CREATE INDEX IF NOT EXISTS ledger_snapshots_date_result
  ON ledger_snapshots (game_date, result);

ALTER TABLE ledger_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ledger_snapshots_public_read ON ledger_snapshots;
CREATE POLICY ledger_snapshots_public_read
  ON ledger_snapshots FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS ledger_snapshots_service_role_write ON ledger_snapshots;
CREATE POLICY ledger_snapshots_service_role_write
  ON ledger_snapshots FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE ledger_snapshots IS
  'Immutable record of every Ledger suggestion at generation. Grader backfills W/L + unit_pnl. Powers The Ledger record display (transparency parity with Sharp Card).';

COMMIT;

NOTIFY pgrst, 'reload schema';
