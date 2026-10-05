# Workflow scheduling — why jobs run late, and the real fix

**2026-10-05.** Andy: *"find out way for pipeline and workflows to run on time
efficiently and consistently."*

## The measurement

Actual fire time minus scheduled time, from 870 `workflow_heartbeat` start rows:

| workflow | runs | median | p10 | p90 | worst |
|---|---|---|---|---|---|
| nightly_cross_sport | 10 | **+334m** | +256m | +395m | +395m |
| daily_card | 21 | **+331m** | +244m | +383m | +511m |
| mlb_grade_overnight | 88 | **+317m** | +256m | +426m | +556m |
| nhl_pipeline | 32 | +281m | +222m | +356m | +449m |
| nba_pipeline | 26 | +263m | +212m | +355m | +438m |
| mlb_pipeline | 118 | +260m | +167m | +378m | **+664m** |
| nfl_pipeline | 30 | +251m | +129m | +348m | +380m |
| ncaab_pipeline | 82 | +227m | +141m | +326m | +440m |
| ncaaf_pipeline | 19 | +212m | +126m | +276m | +280m |
| ufc_pipeline | 16 | +203m | +128m | +285m | +317m |

**Every workflow is late. Median 3.4–5.6 hours. Worst case 11 hours.**

And on 2026-10-05, three workflows had not started in over 26 hours
(`nba_pipeline` 27.2h, `nightly_cross_sport` 26.7h, `ncaaf_pipeline` 26.3h)
with nothing anywhere reporting it.

## Why

GitHub Actions treats `schedule` events as **best-effort**. They are queued at
low priority and are delayed — or dropped entirely — when the platform is
busy. This is documented behaviour, not a bug in our YAML, and it affects
every repo on shared runners. Nothing in our grading logic can compensate for
a job that never started.

This is the root cause of the symptom Andy hit every morning: the "overnight"
grader meant to finish by 5:30am ET routinely ran between 8am and noon ET, so
he opened the app before anything had graded.

## What was done (mitigations, shipped)

1. **`resolve_all_now.py` + `resolve_chain.yml`** — the whole resolution chain
   in dependency order, every 3 hours. Small, cheap, idempotent (only ever
   fills NULLs), so many attempts cost little and any ONE landing leaves the
   chain fully resolved.
2. **Schedules shifted earlier by the measured delay.** `mlb_grade_overnight`
   moved from 06:30/08:30/09:30 UTC to 01:10/03:10/04:10 UTC so the actual
   landing is near the originally intended time. Firing early is harmless
   here — an early run simply grades less and a later slot finishes it.
   Firing late is what costs us.
3. **`watchdogs.check_workflow_stale`** — alerts when a scheduled workflow has
   not run inside its expected cadence. A pipeline that silently stops is
   indistinguishable from one with nothing to do, which is why this went
   unnoticed for weeks. Never-run is a WARNING (a newly added workflow
   legitimately has no history); CRITICAL is reserved for a job that ran and
   then stopped.

## The real fix (NEEDS ANDY — not done)

`workflow_dispatch` events are **not** subject to the schedule queue. A
manually or API-triggered run starts promptly. So the reliable pattern is to
move the clock OFF GitHub:

```
reliable scheduler  ->  GitHub REST API  ->  workflow_dispatch  ->  runs now
```

```
POST /repos/TheSweatLocker/sweat-locker/actions/workflows/{file}/dispatches
Authorization: Bearer <PAT with actions:write>
{"ref":"main"}
```

Options for the scheduler, cheapest first:

| option | reliability | what it needs |
|---|---|---|
| **Supabase `pg_cron` + `pg_net`** | high | DDL (Andy applies), a GitHub PAT in Supabase secrets |
| Supabase Edge Function on a schedule | high | same PAT; we already have `supabase/functions` |
| External cron (cron-job.org, Cloudflare Worker) | high | PAT held off-platform |
| Keep GitHub `schedule` | **low — measured above** | nothing |

Recommended: **Supabase `pg_cron` calling the dispatch endpoint**, because the
database is already the thing we trust to be up, and the secret can live in
Supabase secrets alongside the provider keys (consistent with the rule that
keys live only in Supabase edge-function secrets, GitHub Actions secrets, and
`mlb_pipeline/.env`).

Blocking items for Andy:
- create a GitHub PAT scoped to `actions:write` on this repo
- store it as a Supabase secret (never in the repo, never in root `.env`)
- approve the `pg_cron` migration

Until that lands, the three mitigations above are what keeps the record
current, and `check_workflow_stale` is what tells us when they have not.


---

## 2026-10-05 UPDATE — TWO DIFFERENT FAILURES, and I conflated them

Andy pasted this from a failed `mlb_refit_weekly` / `mlb_pipeline` run:

```
The job was not acquired by Runner of type hosted even after multiple attempts
Internal server error. Correlation ID: fafd1b36-fef5-43ac-a58b-d32df8fb53f0
```

That is **not** the delay problem above. It is a different failure and it
matters, because **my recommended fix does not solve it.**

| failure | what happens | fixed by `workflow_dispatch`? |
|---|---|---|
| **schedule delay** — job queued late | runs, but 3-6h late | **yes** — dispatch skips the schedule queue |
| **runner starvation** — "not acquired" | never runs at all, zero steps | **NO** — a dispatched job needs a runner too |

A dispatch-triggered job still has to be assigned a hosted runner. If GitHub
cannot assign one, the dispatch fails exactly the same way. Correcting that
here so nobody implements pg_cron expecting it to fix red runs.

### The likely cause of starvation: we ask for too many runners

Counted across all 24 scheduled workflows:

| workflow | runner acquisitions/day |
|---|---|
| mlb_line_poller | **72.0** |
| prop_close_freeze | **52.3** |
| multisport_line_poller | **37.0** |
| keep_alive | **24.0** |
| mlb_imminent_refresh | 22.0 |
| mlb_oddscrowd_refresh | 12.0 |
| ...18 others | 45.1 |
| **TOTAL** | **266.4 / day** |

**The top four are 185 of 266 (69%)** and every one of them is a poller or a
keep-warm ping — short jobs whose whole purpose is an HTTP call. 266
acquisitions a day from one account is the profile that gets throttled.

### Options, honestly ranked

1. **Move the pollers off Actions.** A line poller does an HTTP fetch and a
   DB write. A Supabase edge function or `pg_cron` + `pg_net` does that with
   **no runner at all**. Moving the top four would cut demand ~69% and is the
   single highest-leverage change available. `keep_alive` (24/day) almost
   certainly should not be a GitHub job in the first place.
2. **Self-hosted runner.** Eliminates both starvation and queue delay
   outright. A cheap VPS or an always-on box. Real fix, costs money/setup.
3. **Consolidate schedules.** `nfl_pipeline` has 10 cron slots,
   `mlb_refresh_missing` has 5, `ncaaf_pipeline` has 5. Fewer, fatter runs.
4. `workflow_dispatch` via pg_cron — still worth doing for the *timing*
   problem, but it is now explicitly NOT the fix for red runs.

### Done 2026-10-05: pinned the runner image

All **27** `runs-on` entries moved from `ubuntu-latest` to **`ubuntu-24.04`**.

`ubuntu-latest` migrates to Ubuntu 26 on **2026-10-19** — 14 days out, and
**NBA opens 10-21**. Every workflow would have silently changed OS two days
before a launch, with playwright/apt/python differences landing unannounced.
Pinning is free and removes a dated surprise; unpin deliberately after
testing, not by default.
