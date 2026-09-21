-- 20260921b — third independent money-flow source (Fade The Public Analytics)
--
-- WHY. OddsCrowd moved its splits table to client-side rendering on
-- 2026-09-21, so plain-HTTP scraping can no longer see money%/bets% at all
-- (see project_oddscrowd_client_render_921). That left exactly ONE money-flow
-- source, fadereport, and the sharp-money discipline rule wants 2+ contrarian
-- sources before it will FADE — a gate that can never be satisfied by a single
-- feed. This restores corroboration.
--
-- WHY THIS SOURCE. It exposes a public JSON API (no auth, no paywall) carrying
-- BOTH halves of the split — bet% and money% — for moneyline, spread and
-- totals. Validated 2026-09-21: NFL 96/96 percentage pairs summed to 100 with
-- zero missing, MLB 18/18. And it is genuinely independent of fadereport
-- rather than a mirror: on NYG@LAR moneyline it read bets 16% / money 23%
-- where fadereport read 15% / 26% — same market sampled differently, which is
-- the whole point of a corroborating source. Coverage is also far better on
-- football: 16 NFL games today versus fadereport's 1.
--
-- KNOWN GAP. No NHL endpoint (the site covers NFL/NCAAF/NBA/MLB/NCAAB/Golf).
-- fadereport still carries NHL, so hockey keeps one source into the Oct 8
-- opener. NBA is present but shows only stale offseason rows until the season
-- starts — expected, not a failure.

-- ── 1. Signals table ──────────────────────────────────────────────────────
-- Deliberately mirrors fadereport_signals column-for-column so downstream
-- consumers can treat the two uniformly instead of learning a second shape.
CREATE TABLE IF NOT EXISTS fadethepublic_signals (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE        NOT NULL,
    sport           TEXT        NOT NULL,
    game_id         TEXT,
    away_team       TEXT,
    home_team       TEXT,
    game_time_et    TEXT,
    market          TEXT        NOT NULL,   -- 'ml' | 'rl' | 'total'
    sharp_side_raw  TEXT,
    sharp_side_norm TEXT,                   -- 'away'|'home'|'over'|'under'|''
    strength_pts    INTEGER,                -- abs(money% - bets%) on sharp side
    strength_tier   TEXT,                   -- 'strong' >=20 | 'lean' >=10 | 'weak'
    bets_side_pct   NUMERIC,
    money_side_pct  NUMERIC,
    bets_other_pct  NUMERIC,
    money_other_pct NUMERIC,
    current_line    TEXT,
    current_odds    TEXT,
    reasoning       TEXT,
    raw_snapshot    JSONB,
    fetched_at      TIMESTAMPTZ DEFAULT now(),
    generated_at    TIMESTAMPTZ DEFAULT now()
);

-- One row per (snapshot, sport, game, market) so a re-run inside the same day
-- updates in place rather than stacking duplicates — the scraper upserts on
-- this key. Uses COALESCE-free columns because game_id can be NULL when team
-- matching refuses (better a NULL than a wrong attribution).
CREATE UNIQUE INDEX IF NOT EXISTS ftp_signals_unique
    ON fadethepublic_signals (snapshot_date, sport, COALESCE(game_id, ''), market,
                              COALESCE(away_team, ''), COALESCE(home_team, ''));

CREATE INDEX IF NOT EXISTS ftp_signals_sport_date
    ON fadethepublic_signals (sport, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS ftp_signals_game
    ON fadethepublic_signals (game_id) WHERE game_id IS NOT NULL;

-- ── 2. Archive columns ────────────────────────────────────────────────────
-- public_splits_archive stores each source side-by-side per
-- (sport, game_id, market, side, ts) so the pattern miner can score
-- "sources agreed / disagreed / one-loud" over long windows. Adding a third
-- source means three more columns, matching the existing oc_*/fr_* naming.
ALTER TABLE public_splits_archive
    ADD COLUMN IF NOT EXISTS ftp_money_pct  NUMERIC,
    ADD COLUMN IF NOT EXISTS ftp_bets_pct   NUMERIC,
    ADD COLUMN IF NOT EXISTS ftp_divergence NUMERIC,
    -- cleatz has been writing cleatz_signals since 2026-08-15 and is healthy
    -- (47 NFL / 119 NCAAF / 9 MLB on 2026-09-21), but archive_public_splits
    -- only ever merged OC + FR, so none of it reached this table. A live
    -- source the money-flow consumer could not see. Same class as the dead
    -- resolvers found on 09-20: the scraper worked, the plumbing didn't.
    ADD COLUMN IF NOT EXISTS cz_money_pct   NUMERIC,
    ADD COLUMN IF NOT EXISTS cz_bets_pct    NUMERIC,
    ADD COLUMN IF NOT EXISTS cz_divergence  NUMERIC;

COMMENT ON COLUMN public_splits_archive.ftp_money_pct IS
    'Fade The Public Analytics: share of DOLLARS on this side (2026-09-21+)';
COMMENT ON COLUMN public_splits_archive.ftp_bets_pct IS
    'Fade The Public Analytics: share of TICKETS on this side (2026-09-21+)';
COMMENT ON COLUMN public_splits_archive.ftp_divergence IS
    'ftp_money_pct - ftp_bets_pct. Positive = dollars outrunning tickets.';
COMMENT ON COLUMN public_splits_archive.cz_money_pct IS
    'cleatz: share of DOLLARS on this side (backfilled into archive 2026-09-21)';
COMMENT ON COLUMN public_splits_archive.cz_bets_pct IS
    'cleatz: share of TICKETS on this side (backfilled into archive 2026-09-21)';
COMMENT ON COLUMN public_splits_archive.cz_divergence IS
    'cz_money_pct - cz_bets_pct. Positive = dollars outrunning tickets.';

-- ── 3. RLS ────────────────────────────────────────────────────────────────
-- Same posture as the other signal tables: service-role writes, no anon read.
-- Left closed deliberately — this is pipeline input, not a user-facing
-- surface, and the 2026-09-09 audit closed 4 tables that were open by default.
ALTER TABLE fadethepublic_signals ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
