# 2026-08-28 e — NFL team defense stats: add missing YPG cols + case fix

Paste this block in Supabase SQL editor (Run once). Idempotent.

After this lands:
```
cd mlb_pipeline
python nfl_team_defense_backfill.py --all-seasons
```

```sql
-- Add per-facet YPG columns (original 20260828c dropped them, or an
-- older draft was applied).
ALTER TABLE nfl_team_defense_stats
    ADD COLUMN IF NOT EXISTS def_pass_ypg NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS def_rush_ypg NUMERIC(6, 2);

-- Case-fix existing rows: nfl_team_stats stores season_type='REG'
-- (uppercase); our backfill wrote 'reg' (lowercase). Downstream
-- joins fail on the case mismatch.
UPDATE nfl_team_defense_stats
   SET season_type = 'REG'
 WHERE season_type = 'reg';

-- Match nfl_team_stats convention for future writes.
ALTER TABLE nfl_team_defense_stats
    ALTER COLUMN season_type SET DEFAULT 'REG';

NOTIFY pgrst, 'reload schema';
```
