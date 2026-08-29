# 2026-08-29 — Backfill home_spread_covered from spread_result

The mlb_game_results grader was writing `spread_result` for years but
never writing `home_spread_covered`. Fixed in code today (commit
3a081051), but 1548 historical rows still have NULL despite the info
being derivable. This SQL backfills them in one atomic op.

Paste in Supabase SQL editor. Idempotent.

```sql
UPDATE mlb_game_results
   SET home_spread_covered = CASE spread_result
       WHEN 'home_covered' THEN TRUE
       WHEN 'away_covered' THEN FALSE
       ELSE home_spread_covered
   END
 WHERE home_spread_covered IS NULL
   AND spread_result IN ('home_covered', 'away_covered');

NOTIFY pgrst, 'reload schema';
```

After this, downstream code that reads `home_spread_covered` (audit_
tier_calibration, mine_line_patterns) will find fully-populated rows
back to whenever spread_result grading started.
