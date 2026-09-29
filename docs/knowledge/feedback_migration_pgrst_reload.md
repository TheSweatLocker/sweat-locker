---
name: feedback-migration-pgrst-reload
description: "Every Supabase migration that adds/changes columns MUST append `NOTIFY pgrst, 'reload schema';` at the end. Without it, PostgREST's schema cache stays stale for ~10min and silently 400s every insert/update touching the new column."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Hard rule**: every Supabase migration that touches a table schema (ALTER TABLE / ADD COLUMN / DROP COLUMN / etc.) MUST end with:

```sql
NOTIFY pgrst, 'reload schema';
```

**Why**: 2026-05-24 incident — `20260524_game_context_ops_recency.sql` added 6 columns to `mlb_game_context`. Migration applied successfully but PostgREST's schema cache wasn't refreshed. Result: every game_context upload returned `PGRST204: Could not find the 'away_ops_last14' column of 'mlb_game_context' in the schema cache`. Zero rows written for ~12 hours. App showed all games as PASS with no Sweat. Sweat card fell back to "light slate — 2 games" copy. The morning was lost to the user discovering and diagnosing this.

PostgREST auto-refreshes its cache eventually (~10 min in practice, sometimes longer), but cron jobs that fire seconds after the migration always lose this race.

**How to apply**:

1. When writing a new migration SQL file that touches schema, ALWAYS end with:
   ```sql
   -- Force PostgREST to reload its schema cache so writes don't 400 with PGRST204
   NOTIFY pgrst, 'reload schema';
   ```
2. When applying a migration manually via Supabase SQL editor, include the NOTIFY line.
3. When reviewing existing migrations, retroactively add the NOTIFY where missing.
4. Diagnosis pattern: if pipeline upload starts returning `PGRST204` or `column not found in schema cache`, the cause is ALWAYS the schema cache, not the actual schema. Verify the column exists with a direct SELECT first to confirm it's a cache miss vs. an unapplied migration.

**Related**: [[feedback_backside_dictates_app_renders]] — silent backend failure cascading to broken app UI is the recurring theme.
