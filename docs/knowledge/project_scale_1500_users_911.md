---
name: project-scale-1500-users-911
description: Queued discussion — how to ensure app survives 1500 concurrent Sunday morning users; crash-handling posture
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-11T12:19:46.481Z
---

**User's ask (2026-09-11 morning):** "Say having 1500 users, how do i get to point as app where if 1500 users are using it on a sunday morning, how do i ensure app wont crash, i mean what happen if it does crash in that scenario"

**Why:** Post-launch scale planning. Sunday morning = MLB slate + NFL early-window = peak concurrent load pattern. Andy needs to know both (a) capacity planning to prevent crashes and (b) failure mode + recovery when they happen anyway.

**How to apply:** Full discussion queued. Cover architecture bottlenecks + monitoring + graceful degradation in that order. My initial read shipped in the 2026-09-11 morning message — build from that.

## Priority order for pre-scale work (draft)

1. **Sentry / crash monitoring** — needed BEFORE first 100 users. You cannot fix what you cannot see.
2. **Supabase Pro tier upgrade** ($25/mo) — needed around 200-300 concurrent users. Free tier caps at ~200 DB connections + 5 GB bandwidth/mo.
3. **RPC caching pattern** — `get_todays_sweat_card` and other front-page reads should hit a materialized view or a `_current` table row, not recompute on every call. 1500 concurrent reads recomputing = DB pain.
4. **Client-side error boundaries + retry-with-backoff** — RN app currently likely shows infinite spinner when Supabase 429s. Need "trouble loading, tap to retry" state and offline `AsyncStorage` cache of last-known-good card.
5. **Split pipeline / user-facing DB** — long-term: pipeline writes to a staging schema, user reads from a read-only frozen schema. Prevents table locks during pipeline runs.

## Crash scenarios + user impact
- **Client JS crash** (RN error boundary miss) → native crash dialog, app closes. iOS/Android auto-report to Apple/Google Console. Without Sentry we learn about it from user complaints only.
- **Supabase 429** → spinner forever if no retry logic. User assumes app broken.
- **Supabase down** → same as above unless offline cache exists.
- **Pipeline hung / stale data** → NOT a crash — app shows yesterday's card. Users still get value, just stale picks.

## Related
- [[project_supabase_health_audit_909]] (existing RLS work)
- [[project_pipeline_overhaul_909]]
- [[project_v1_0_1_client_priorities]]
