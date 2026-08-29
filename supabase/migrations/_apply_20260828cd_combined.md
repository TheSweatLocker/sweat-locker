# 2026-08-28 c+d — NFL + NCAAF team defense stats tables

Paste this SINGLE block in the Supabase SQL editor (Run once). Idempotent —
safe to re-run.

After this lands, run the two backfills (or wait for the next scheduled
NFL / NCAAF cron):

```
cd mlb_pipeline
python nfl_team_defense_backfill.py --all-seasons
python ncaaf_team_defense_backfill.py --all-seasons
python _seed_defense_stats_signals_2026-08-28.py
```

```sql
-- ─────────────────────────────────────────────────────────────
-- 1. NFL team defense stats
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nfl_team_defense_stats (
    team              TEXT NOT NULL,
    season            INTEGER NOT NULL,
    season_type       TEXT NOT NULL DEFAULT 'reg',
    games             INTEGER NOT NULL,
    def_ppg           NUMERIC(5, 2),
    def_ypg           NUMERIC(6, 2),
    def_pass_ypg      NUMERIC(6, 2),
    def_rush_ypg      NUMERIC(6, 2),
    def_pass_epa_allowed NUMERIC(6, 4),
    def_rush_epa_allowed NUMERIC(6, 4),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team, season, season_type)
);

CREATE INDEX IF NOT EXISTS idx_nfl_team_defense_season
    ON nfl_team_defense_stats (season DESC, def_ppg ASC);

-- ─────────────────────────────────────────────────────────────
-- 2. NCAAF team defense stats
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ncaaf_team_defense_stats (
    team                    TEXT NOT NULL,
    season                  INTEGER NOT NULL,
    season_type             TEXT NOT NULL DEFAULT 'regular',
    games                   INTEGER NOT NULL,
    def_ppg                 NUMERIC(5, 2),
    def_pass_epa_allowed    NUMERIC(6, 4),
    def_rush_epa_allowed    NUMERIC(6, 4),
    def_success_rate_allowed NUMERIC(5, 4),
    def_explosiveness_allowed NUMERIC(5, 4),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team, season, season_type)
);

CREATE INDEX IF NOT EXISTS idx_ncaaf_team_defense_season
    ON ncaaf_team_defense_stats (season DESC, def_ppg ASC);

NOTIFY pgrst, 'reload schema';
```
