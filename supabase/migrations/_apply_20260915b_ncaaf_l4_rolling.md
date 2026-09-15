# 2026-09-15 b — NCAAF ctx L4 rolling form (v1.06)

Paste this SINGLE block in the Supabase SQL editor (Run once). Idempotent —
safe to re-run.

After this lands, run the NCAAF context script to stamp the new columns
on the current-slate rows (or wait for the next scheduled ncaaf_pipeline
cron), then rebuild the LR total model with rolling features re-enabled:

```
cd mlb_pipeline
python ncaaf_game_context.py           # stamps home_l4_*/away_l4_* on live rows
python ncaaf_total_logreg_train.py     # retrain w/ rolling features (v1.06)
```

```sql
ALTER TABLE ncaaf_game_context
  ADD COLUMN IF NOT EXISTS home_l4_ppg        NUMERIC,
  ADD COLUMN IF NOT EXISTS home_l4_pa         NUMERIC,
  ADD COLUMN IF NOT EXISTS home_l4_total_avg  NUMERIC,
  ADD COLUMN IF NOT EXISTS home_l4_over_rate  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_ppg        NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_pa         NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_total_avg  NUMERIC,
  ADD COLUMN IF NOT EXISTS away_l4_over_rate  NUMERIC;

NOTIFY pgrst, 'reload schema';
```
