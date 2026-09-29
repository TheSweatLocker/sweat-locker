---
name: project-supabase-health-audit-909
description: "🚨 9/8 evening: Supabase health audit — 65 advisor issues (4 CRITICAL RLS fixed + narrowed for app), 63.1% success rate root causes identified"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T19:01:57.680Z
---

**User surfaced 9/8 evening:** Supabase dashboard shows alarming numbers.

## Snapshot (last 24h)

- **504,153 total requests** — high volume, healthy compute
- **63.1% success rate** — 🚨 ~186k failed requests
- **411,201 API Gateway requests** · **93,485 warnings** · **8 errors**
- **92,691 Postgres warnings** · 0 Postgres errors
- **65 Advisor issues** total (only saw top 4 = CRITICAL RLS)

## Root causes identified 9/8 evening

### 🚨 #1 — MY OWN RLS lockdown broke app writes (FIXED)
Migration `20260909a_rls_close_public_holes.sql` locked `outcomes` +
`kenpom_cache` to anon. But the app writes to both with anon key:
- `app/index.tsx:11537 + :11568` → `outcomes.insert` (every bet mark)
- `app/index.tsx:2933` → `kenpom_cache.upsert` (NCAAB cache)
- `app/index.tsx:2854` → `kenpom_cache.select`

**Fix (applied):** `20260909b_rls_narrow_policies_for_app_writes.sql`
grants narrow anon INSERT+SELECT on outcomes, SELECT+INSERT+UPDATE on
kenpom_cache, while keeping RLS enabled. ufc_fight_results +
mlb_catcher_framing stay locked (no app usage).

### 🔴 #2 — AdminNoticeBanner 60s poll (FIXED)
`app/components/AdminNoticeBanner.tsx` polled admin_notice every 60s
= 1440 requests/user/day per mount. Reduced to 5min → 288/day (5x cut).
Commit `0282d7ce`.

### 🔵 #3 — MLB pipeline manual triggers
24 manual `mlb_pipeline` triggers on 9/8 alone. Each fires the full
pipeline (LLM + externals + all writes). This is likely user rescue-
triggering when things looked wrong. Now that auto-repair works
better, these should stop naturally.

### ⚪ #4 — App polling patterns (audit needed)
- `mlb_game_context` 90s auto-refresh (app/index.tsx:2310)
- Multiple game_context.py refresh loops
- Some off-season sports getting hit despite no data

## Config UI sections migrations applied 9/8

User applied all `20260908a-e_config_ui_sections*.sql` migrations. Now
30 rows populate the table, anon can read, `useSectionEnabled` hook
returns real values from DB (was silent-failing to defaultVal=true).

## What's still queued

### 61 remaining Advisor issues
Not audited. Likely categories:
- More RLS holes on tables
- Missing indexes on hot queries
- Unused indexes wasting storage
- Long-running queries
- Function security_definer misuse

### Long-term: move outcomes + kenpom_cache writes server-side
Anon key writing to prod tables is architectural smell. Should be
edge functions with service_role. Filed under [[project_pipeline_overhaul_909]] P2.

## What was FIXED this session (recap)
- 4 CRITICAL RLS holes closed
- App-required narrow policies restored
- AdminNoticeBanner 5x cadence reduction
- All 4 config_ui_sections migrations applied by user

## Related memory
- [[project_pipeline_overhaul_909]] — deeper pipeline audit
- [[project_rls_resolved_818]] — prior RLS work
- [[project_data_infrastructure_priorities_908]] — foundation-first stack
- [[feedback_migration_pgrst_reload]] — migration hygiene
