---
name: project-workflow-trigger-layers-918
description: MLB pipeline push trigger removed 9/18 — it caused every trigger bug since 9/01. Commits no longer refresh the slate; use workflow_dispatch.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-18T21:57:50.756Z
---

**The MLB pipeline no longer runs on push. Committing code does not
refresh the slate — use `workflow_dispatch` (Actions tab → Run
workflow).** Changed 2026-09-18, commit ec6f1f63.

**Why:** Four months of trigger patches were each fixing damage from
the previous one:

| Date | Added | Because | Broke |
|------|-------|---------|-------|
| 9/01 | `push:` trigger | GH cron 4-7hrs late | 23 concurrent runs in 4hrs racing Supabase upserts |
| 9/08 | `concurrency:` group | the 23-run pileup | GH cancelled *queued scheduled* runs when a push queued behind |
| 9/11 | group **split by trigger source** | those cancellations | scheduled + push landed in different groups → **ran simultaneously** |
| 9/17 | `paths` + `paths-ignore` | over-triggering | invalid YAML (GHA forbids both), 500+ runs failed validation, reverted |

On 9/18 runs #1094 (scheduled) and #1095 (push) were confirmed in
progress at the same time — each 1h12m–1h40m, both doing
wipe-and-refresh on the same tables. That is a direct cause of mid-day
tier drift, badge inconsistency, and records changing while users watch.

**The fix was deleting layers 1 and 3, not adding a fourth.** Removing
the push trigger makes the trigger-source split unnecessary. Net −7
lines. Cron lateness is covered by the four existing scheduled triggers
(6:00 / 7:15 / 8:30am / 2pm ET); manual refresh is what
`workflow_dispatch` is for.

**How to apply:**
- Do NOT re-add a push trigger to a long-running data pipeline. A code
  commit is not a reason to re-run 90 minutes of data work.
- Before adding any concurrency guard, MEASURE: compare the minimum gap
  between cron fire times against observed runtime. On 9/18 only
  `nfl_pipeline` (10min gap vs 49min runtime → up to 5 concurrent runs)
  and `ncaaf_pipeline` (45min gap vs 40m25s, 5min margin) justified
  one. The other 7 unguarded workflows were measured and left alone.
- **Always `yaml.safe_load` every workflow file before pushing** and
  assert `name`, triggers and `jobs` all parse. When the 9/17 YAML
  broke, GH silently ran 0 jobs and the run name fell back to the file
  path — that fallback name is the tell.

Related: [[feedback-fix-at-root-three-parts]],
[[feedback-always-push-after-commit]]
