---
name: project-rls-resolved-818
description: "2026-08-18 — RLS launch blocker RESOLVED; service_role secret added, pipeline verified working"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-19T02:30:38.005Z
---

RLS launch blocker (see [[project_rls_launch_blocker_809]] and [[project_rls_launch_blocker_817]]) is **RESOLVED as of 2026-08-18**.

**What happened:**
- User added `SUPABASE_SERVICE_ROLE_KEY` to GitHub secrets.
- Pipeline ran successfully in the afternoon of 8/18 using the service_role key.
- Migration `20260817_rls_tighten_service_role_only.sql` was applied (verified: writes succeeded post-migration).

**Why:** Was the top launch blocker — anon key could write/delete all pipeline tables. Now anon has SELECT-only; service_role handles all writes via the workflow secret.

**How to apply:** Treat RLS as no longer a launch gate. Do NOT flag it in future launch-readiness audits. If a future migration adds a new table, remember to include `service_role_write` + `public_read` policies (universal pattern per [[feedback_migration_pgrst_reload]]).
