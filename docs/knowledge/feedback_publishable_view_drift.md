---
name: publishable-view-drift
description: 🚨 CREATE OR REPLACE VIEW replaces — never merges. Any migration touching v_mlb_props_publishable MUST carry forward every prior WHERE clause. Third drift incident 2026-09-17.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T17:40:58.265Z
---

**Rule:** Any migration that touches `v_mlb_props_publishable` (or any
CREATE OR REPLACE VIEW) MUST carry forward EVERY prior WHERE clause.
Verify against `git log --oneline supabase/migrations/*props_publishable*`
before writing the new view SQL — enumerate every rule the current view
enforces and include ALL of them, plus the new rule.

**Why:** Third documented drift incident.

- 2026-09-15c added Rule 4 (batter-family ban)
- 2026-09-15d added Rule 5 (tier whitelist)
- 2026-09-15e added Rule 6 (hits_under LEAN ban)
- 2026-09-15f added Rule 4b (hits_over ban)
- 2026-09-16h swapped Rule 4 selective un-ban (dropped none)
- 2026-09-17a EMERGENCY restored full Rule 4 ban but **silently dropped
  Rules 4b, 5, and 6** — only Rules 1-4 survived
- Consequence: 282 hits props flooded publishable feed as COVERAGE,
  282 hits_over PRIMEs would have re-appeared if LR re-promoted them
- 2026-09-17b restored all rules in one consolidated view + this memory

**How to apply:**

Before writing any migration that touches `v_mlb_props_publishable`:

1. Run `git log --oneline -- supabase/migrations/*props_publishable*`
   to enumerate the full history
2. Read the LATEST migration to see the current WHERE clause set
3. In the new migration, start FROM the latest full definition and
   ADD or MODIFY specific rules — never write from scratch
4. In the migration comment header, explicitly list every rule the
   view enforces after the change (Rules 1, 2, 3, 4, 4b, 5, 6, …)
5. Post-apply, run `psql -c "SELECT count(*) FROM v_mlb_props_publishable
   WHERE prop_type IN (banned families here)"` — must return 0

This same class of drift produced the composer-vs-view split earlier
9/17 that led to `mlb_pipeline/prop_ban_policy.py` (shared single source
of truth for composers). The view is the second half of that pattern —
it lacks a shared-policy abstraction because it's SQL, not Python.

**Structural fix (queued, not shipped):** move all view WHERE clauses
into a Postgres immutable function `is_publishable_mlb_prop(prop_type,
tier, signals, jerry_verdict) → bool` so migrations can add rules to
the function body without rewriting the view. Then the view is:
`WHERE is_publishable_mlb_prop(p.prop_type, p.tier, p.signals, pj.call_verdict)`.
See project_prop_publishable_view_drift_917.

**Related:**
- [[feedback_prop_family_ban_three_layer]] — the composer half of this
  pattern (each composer must import the shared policy)
- [[feedback_migration_pgrst_reload]] — every migration ends with
  `NOTIFY pgrst, 'reload schema';`
