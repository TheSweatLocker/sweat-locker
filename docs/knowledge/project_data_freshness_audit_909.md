---
name: project-data-freshness-audit-909
description: "🎯 9/8 audit: per-sport data-freshness across 7 sports. MLB healthy; NFL/NCAAF reads are JIT by design; NBA/NHL/NCAAB pre-season; UFC between events."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T19:02:22.214Z
---

**User surfaced 9/8 evening:** "Do we need to dig into data path flow for everything on cadence?"

**Answer: yes.** Audit done tonight, results below.

## Snapshot table

| Sport | latest_ctx | latest_reads | latest_results | Status |
|-------|-----------|--------------|----------------|--------|
| MLB   | 2026-09-08 | 2026-09-08 | 2026-09-08 | ✅ CURRENT |
| NFL   | 2027-01-10 | 2026-09-15 | 2027-01-10 | ✅ CURRENT (ctx = full season pre-loaded; reads = JIT) |
| NCAAF | 2026-09-26 | 2026-09-13 | 2026-12-12 | ✅ CURRENT (same JIT pattern) |
| NCAAB | None | None | 2025-04-08 | ⏸ PRE-SEASON (Nov 3) |
| NBA   | None | None | 2025-06-22 | ⏸ PRE-SEASON (Oct 24) |
| NHL   | None | None | 2025-04-17 | ⏸ PRE-SEASON (Oct 8) |
| UFC   | 2026-09-05 | 2026-09-05 | 2026-09-05 | ⏸ BETWEEN EVENTS |

## What's healthy
- **MLB** — 92 heartbeat starts in 3d. 628 external picks in 3d. Reads current.
- **JIT reads for football** — NFL/NCAAF reads only generated for current + next week. Weeks 3-18 have context rows but no reads by design. Not a gap.
- **Pre-season sports** — NCAAB/NBA/NHL all show empty ctx/reads/heartbeat which matches their roadmap. NHL resolver still unbuilt (project_nhl_resolver_build_908).

## What needs attention
- **Non-MLB heartbeat empty (as of 9/8 evening).** Fixed by tonight's heartbeat parity commit (7bbfd521) but those pipelines haven't run since. Next scheduled crons will populate:
  - NCAAF: Sat 12:15 UTC (was 12:00, staggered)
  - NFL: Wed/Thu/Sun 12:10 UTC
  - NHL: daily 12:05 UTC (pre-season, may just be a scheduler ping)
  - NBA: daily 12:00 UTC (pre-season)
  - UFC: Wed 22:00 + Fri/Sat/Sun 14:00
  - NCAAB: Tue 15:00 + Wed/Fri/Sat/Sun rotations
- **First non-MLB heartbeat check** should be after each above fires. If still empty by 9/10, dig deeper — either RLS on workflow_heartbeat blocks non-MLB, or the crons are silently skipping.
- **UFC has no upcoming events in ufc_picks.** Last event 9/5. Next weekly pull runs Wed 9/10 22:00 UTC — should populate next event's data. If still empty by 9/11, ufc_pipeline may be broken.

## Verification queue (post-first-run of each pipeline)
```bash
# Run 9/9 morning to see if heartbeat populated overnight
python -c "check workflow_heartbeat for non-MLB rows since 2026-09-09"
# Run 9/10 morning after Wed NFL cron
# Run 9/11 morning after Wed UFC cron
# Run 9/13 afternoon after Sat NCAAF cron
```

## Diagnostic script
Saved to: `scratchpad/data_freshness_audit.py`
Run any time to get the freshness snapshot table.

## Related memory
- [[project_pipeline_overhaul_909]] — heartbeat parity landed tonight
- [[project_nhl_resolver_build_908]] — NHL resolver still unbuilt
- [[project_ufc_model_broken_817]] — UFC pipeline history
- [[project_data_infrastructure_priorities_908]] — Priority stack
