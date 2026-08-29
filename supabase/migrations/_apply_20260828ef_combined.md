# 2026-08-28 e + f — NFL defense fixup + NCAAF stats extension

Paste this block in Supabase SQL editor (Run once). Idempotent.

After this lands:
```
cd mlb_pipeline
# Re-run NFL defense backfill (now writes def_pass_ypg + def_rush_ypg cleanly)
python nfl_team_defense_backfill.py --all-seasons

# Refresh NCAAF stats with the new volumetric fields
python ncaaf_stats_pull.py --start 2022 --end 2026

# Re-run NCAAF defense backfill (now D1-filtered — was 699 rows w/ D2/D3 junk)
python ncaaf_team_defense_backfill.py --all-seasons

# Seed penalty signals (6 rows — 3 per sport)
python _seed_penalty_signals_2026-08-28.py
```

```sql
-- ─────────────────────────────────────────────────────────────
-- 1. NFL defense stats: add missing per-facet YPG cols + case fix
-- ─────────────────────────────────────────────────────────────
ALTER TABLE nfl_team_defense_stats
    ADD COLUMN IF NOT EXISTS def_pass_ypg NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS def_rush_ypg NUMERIC(6, 2);

UPDATE nfl_team_defense_stats
   SET season_type = 'REG'
 WHERE season_type = 'reg';

ALTER TABLE nfl_team_defense_stats
    ALTER COLUMN season_type SET DEFAULT 'REG';

-- ─────────────────────────────────────────────────────────────
-- 2. NCAAF team stats: extend with volumetric + discipline cols
--    Feeds new penalty / third-down / TOP / turnover ctx fields.
-- ─────────────────────────────────────────────────────────────
ALTER TABLE ncaaf_team_stats
    -- Offense volume
    ADD COLUMN IF NOT EXISTS pass_yards          INTEGER,
    ADD COLUMN IF NOT EXISTS pass_tds            INTEGER,
    ADD COLUMN IF NOT EXISTS pass_completions    INTEGER,
    ADD COLUMN IF NOT EXISTS pass_attempts       INTEGER,
    ADD COLUMN IF NOT EXISTS pass_ints           INTEGER,
    ADD COLUMN IF NOT EXISTS rush_yards          INTEGER,
    ADD COLUMN IF NOT EXISTS rush_tds            INTEGER,
    ADD COLUMN IF NOT EXISTS rush_attempts       INTEGER,
    -- Situational
    ADD COLUMN IF NOT EXISTS first_downs         INTEGER,
    ADD COLUMN IF NOT EXISTS third_down_conv     INTEGER,
    ADD COLUMN IF NOT EXISTS third_downs         INTEGER,
    ADD COLUMN IF NOT EXISTS fourth_down_conv    INTEGER,
    ADD COLUMN IF NOT EXISTS fourth_downs        INTEGER,
    -- Discipline
    ADD COLUMN IF NOT EXISTS penalties           INTEGER,
    ADD COLUMN IF NOT EXISTS penalty_yards       INTEGER,
    -- Ball security
    ADD COLUMN IF NOT EXISTS turnovers           INTEGER,
    ADD COLUMN IF NOT EXISTS fumbles_lost        INTEGER,
    -- Time
    ADD COLUMN IF NOT EXISTS possession_time_sec INTEGER,
    -- Defense events (own defense records)
    ADD COLUMN IF NOT EXISTS def_sacks           INTEGER,
    ADD COLUMN IF NOT EXISTS def_ints            INTEGER,
    ADD COLUMN IF NOT EXISTS def_fumbles_rec     INTEGER;

NOTIFY pgrst, 'reload schema';
```
