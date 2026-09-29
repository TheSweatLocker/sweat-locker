---
name: feedback-postgrest-batch-normalize-keys
description: "Any PostgREST batch INSERT/UPSERT must normalize keys across rows — union all keys, backfill missing with None — or it fails with \"All object keys must match\""
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Every PostgREST batch INSERT/UPSERT must normalize keys across rows before posting. Union all keys present on any row, backfill missing keys as None on rows that don't have them. Otherwise the batch fails with the cryptic 400 "All object keys must match".**

**Why:** PostgREST treats a batch INSERT array as a uniform schema — if row[0] has 30 keys and row[1] has 29, PostgREST rejects the entire batch. This bites whenever we add an optional field that gets populated on some rows but not others. Three production incidents now:
- **5/29** generate_props.py: pitcher props picked up book_line via attach_book_lines but batter hits_over/under never did → batch failed, 0 props written
- **6/2 sweat dim** (separate issue but related — different shape mismatch caused empty cron output)
- **6/3** build_hr_watch.py: attach_book_odds attached on 14/15 batters tonight; 1 batter didn't have a batter_home_runs market → batch failed, 0 HR Watch rows written, app surface empty all morning until fix shipped

**How to apply:**
Every time you write code that builds a list of dicts and POSTs to `/rest/v1/<table>`, the same boilerplate goes BEFORE the post:

```python
all_keys = set()
for row in batch:
    all_keys.update(row.keys())
for row in batch:
    for k in all_keys:
        if k not in row:
            row[k] = None
```

It's idempotent, cheap, and immunizes against future optional fields. When code-reviewing any new batch insert (or adding a new field to an existing one), check this normalization is present. If you're adding a new field to an existing payload schema and the normalization isn't there, add the normalization in the same commit.

Tables with this pattern enabled:
- `mlb_pipeline_props` (generate_props.py — `upsert_props`)
- `mlb_hr_watch` (build_hr_watch.py — added 6/3)

Tables that may need it but haven't been verified:
- `mlb_game_context` (write path uses single-row writes today, but if it ever becomes batch, normalize first)
- `daily_dawg` (currently single-row upsert; safe today)
- `mlb_tier_calibration` (batch upsert in audit scripts — verify present)
- `mlb_game_results` (audit scripts — verify present)

**Related:** [[feedback_validate_data_reaches_new_code]] (same family — shipping code that branches on a new field without auditing every consumer), [[feedback_always_push_after_commit]]
