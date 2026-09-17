# Sunday-Morning Capacity Plan (1,500 concurrent users)

Target: app survives Sunday 10 AM ET when MLB early slate + NFL RedZone
window drive peak concurrent load. Current baseline: 61 downloads, 9 trials.
Plan is calibrated for 1,500 concurrent — the aspirational Q1 2027 target.

## Current posture (audited 2026-09-17)

| Item | Status | Notes |
|---|---|---|
| Server-side caching | ✅ | 47 matviews + `get_todays_*` RPCs live (`20260502_slate_date_rpcs.sql`) |
| Client retry+backoff | ✅ | `app/lib/db.ts` — 8s timeout, 1 retry, 500ms backoff |
| Error boundary (RN crash guard) | ✅ | Shipped 2026-09-17 — `app/components/ErrorBoundary.tsx` wired in `_layout.tsx` |
| Sentry crash monitoring | ❌ | Not installed. Highest-priority remaining gap. |
| Offline last-known-good cache | ❌ | RN app has no `AsyncStorage` fallback if Supabase 429s |
| Supabase Pro tier | ❓ | Verify with Andy — needed at 200-300 concurrent |
| Pipeline vs user-facing schema split | ❌ | Long-term (>500 users). Pipeline writes hit user-read tables today |

## Priority order

### 1. Sentry (before 100 concurrent users) — **install this week**

Free tier is 5,000 events/month, enough for a launch cohort. Install:
```
npm install @sentry/react-native
npx @sentry/wizard@latest -i reactNative
```

Set `dsn` in `.env`, initialize in `_layout.tsx` before ErrorBoundary. Wire
`componentDidCatch` in ErrorBoundary to call `Sentry.captureException(err)`.
Confirms our "we don't know what we can't see" gap closes.

### 2. Offline last-known-good cache (before 500 concurrent)

Wrap `dbFetch` results in `AsyncStorage.setItem(key, JSON.stringify(data))`
after every successful read. On timeout/network error, `getItem(key)` and
return the stale copy with a `stale: true` flag. Screens show a subtle
"showing yesterday's card, tap to retry" banner instead of an infinite
spinner. Preserves user experience through Supabase outages.

Key rings: `sweat_card_{date}`, `steam_room_{date}`, `game_reads_{date}`.

### 3. Supabase Pro upgrade ($25/mo)

**Trigger:** DAU sustained >200, OR a spike day (Sunday MLB+NFL) that
pushes concurrent DB connections past ~150. Free tier caps at 200 DB
connections + 5GB bandwidth/mo. Pro raises both by ~5x, adds daily
backups, and lets us configure pgBouncer for connection pooling — the
same 200-concurrent ceiling we'd otherwise hit.

Verify current status via Supabase dashboard. If already on Pro, skip.

### 4. Split pipeline / user-facing schema (>500 concurrent)

Long-term architecture. Today the pipeline writes to the same tables the
app reads. During a heavy write burst (mid-slate rescore, external ingest),
readers can see stale-partial state or wait on row locks.

Fix: pipeline writes to a `_staging` schema; a nightly `REFRESH MATERIALIZED
VIEW CONCURRENTLY` moves ready state to a read-only user-facing schema.
Requires the matview infrastructure we already have + extending it to
sides/props/game reads/potd surfaces.

Not urgent until DAU >500. Design work is what to do first.

### 5. Push-notification stampede protection (once notifications ship)

Not built yet — but when we add push (Sunday-morning "your card is live"),
schedule sends over a 5-minute window. Simultaneous "everyone opens the
app at 10:00 AM" is worse than 1,500 concurrent users generally, because
they all hit the same 3 RPCs within 30 seconds.

## Crash-mode playbook

| Scenario | User sees | Fix path |
|---|---|---|
| Client RN crash | Reload screen (ErrorBoundary) | Sentry → hotfix → OTA Expo update |
| Supabase 429 | "Trouble loading, tap to retry" (once #2 ships) | Wait for backpressure to clear; alert if sustained >5min |
| Supabase down | Same as 429; stale cache serves last-known-good | Check status.supabase.com; contact support if >30min |
| Pipeline hung | Yesterday's card (not a crash) | Ops alert from `check_pipeline_health.py` |
| Stale data | Yesterday's card | Same as above |

## Monitoring

Currently:
- `check_pipeline_health.py` runs at end of each pipeline; hits Slack via
  workflow status
- Supabase logs surface 429 / connection-pool exhaustion via dashboard
- No client-side visibility (fixed once Sentry ships)

Post-Sentry:
- Alert on crash-free session <99.5% for release
- Alert on any error volume >100/hr (typical baseline should be <10/hr)

## References

- [[project_scale_1500_users_911]] — original queued discussion
- [[project_supabase_health_audit_909]] — RLS + API success 63% (needs revisit)
- [[project_pipeline_overhaul_909]] — P0 done 9/15; P1/P2 queued
