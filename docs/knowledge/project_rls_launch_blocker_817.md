---
name: project-rls-launch-blocker-817
description: "2026-08-17 UPDATE — RLS fix code+workflow ready, awaits user actions (GH secret + apply migration)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-17T22:38:10.059Z
---

Follow-up to [[project-rls-launch-blocker-809]] — infra fix built 8/17.

**What's ready:**
1. Migration `20260817_rls_tighten_service_role_only.sql`:
   - Drops permissive `public_write` policies on 35+ tables (was allowing
     anon writes)
   - Creates `service_role_write` (FOR ALL TO service_role) policies
   - Keeps `public_read` (SELECT TO anon) — app can still read
   - Re-enables RLS on tables that had it disabled (jerry_reads, prop_jerry_reads, feature_flags)
2. Workflow YMLs updated (all 11 files):
   `SUPABASE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY || secrets.SUPABASE_KEY }}`
   — pipeline uses service_role when available, falls back to anon otherwise.

**User actions required (in order):**
1. **Add GitHub secret** — Supabase dashboard → Settings → API → copy
   `service_role secret` → GitHub repo → Settings → Secrets → New →
   name `SUPABASE_SERVICE_ROLE_KEY`, paste value.
2. **Trigger one workflow run** and verify writes still succeed (should
   because service_role bypasses RLS entirely).
3. **Apply migration** `20260817_rls_tighten_service_role_only.sql` via
   Supabase dashboard SQL editor.
4. **Verify anon can't write** — attack test:
   ```bash
   curl -X POST 'https://<project>.supabase.co/rest/v1/mlb_pipeline_props' \
     -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <ANON_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"game_date":"2099-01-01","player_name":"TEST","prop_type":"x","direction":"over"}'
   ```
   Should return `401 new row violates row-level security policy` or similar.
5. **Update local .env** — Change `SUPABASE_KEY=<anon>` to
   `SUPABASE_KEY=<service_role>` for manual pipeline runs. Add
   `SUPABASE_ANON_KEY=<anon>` as separate line if the app dev harness
   needs it. Local writes will fail otherwise.

**Rollback if anything breaks:**
Re-apply `20260717_rls_pipeline_write_hotfix.sql` — recreates the
permissive `public_write` policies. Wide-open again but functional.

**Verified 8/17:** anon key can still INSERT to `mlb_pipeline_props`
(status 400 only from column constraints, not RLS). Confirms the fix
is needed.

Related: [[project-rls-launch-blocker-809]] (original threat memo),
[[project-launch-priorities-july]], [[project-pricing-launch-decision]]
