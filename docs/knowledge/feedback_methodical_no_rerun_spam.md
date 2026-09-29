---
name: methodical-no-rerun-spam
description: Batch DB-writing reruns; never spam pipeline/backfill/render re-runs during iterative fixes
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-24T23:27:08.807Z
---

Don't spam pipeline reruns during iterative work. Batch multiple fixes into ONE rerun at end of session, not one after every code change.

**Why:** 2026-08-22 session I ran 5+ full backfill_prop_lookback re-runs, 4 generate_prop_jerry_synthesis --force renders, dedup+wipe script, and full slate ensemble re-scoring — all within a few hours. Supabase sent a Disk IO Budget depletion alert same evening. Iterative "see if it works" reruns on JSONB-heavy tables burn through the IO burst pool fast. User will bump Supabase compute tier at LAUNCH if needed, not before.

**How to apply:**
- Multi-step fixes: accumulate all edits, THEN run the pipeline/backfill/render ONCE.
- Iterating on template output: verify by direct Python demo (`python render_prop_template.py`) not by re-running the full synthesis loop against prod DB.
- Deploying a signal_source change: don't manually re-score to prove it; let next natural cron pick it up unless there's a specific user-visible urgency (e.g. tonight's card).
- When user explicitly asks me to "run it now" or "trigger the pipeline" — that's a green light and safe.
- If I'm about to invoke `--force` or a `--refresh-existing` flag, pause and ask if the natural cron cadence is fine.

Related: [[project_calibration_architecture_805]] — pattern of "ship + let cron carry" already exists in project memory; this feedback formalizes it as a hard rule.

## 2026-08-24 escalation — READ audits count too, not just writes

Second Disk IO warning escalated to full DB degradation (all reads timing out via REST, GH Actions, AND SQL Editor). Sunday afternoon on a game-day. Contributing activity:
- 2 large E2E audit agents run back-to-back (90+ tool uses each, hundreds of queries)
- Iterative timeout-retry loops from failing scripts (10 checks × 3 retries per attempt)
- Ladder queries on non-indexed columns (full table scans = disproportionate IO burn)
- All layered on top of normal cron traffic + weekend games

**Extension to the rule:**
- READ-heavy audit agents count against IO budget too, not just `--force` writes. Batch audits, don't run consecutive comprehensive audits on the same day.
- When user asks for an audit, ask if it can be sampled (e.g. 3 games instead of 10) unless comprehensive coverage matters.
- When a query is slow, don't retry immediately with the same query. Look at the query pattern first — order by non-indexed column? select=*? Fix the query, not add more attempts.
- On a Sunday/game-day, be extra conservative — DB is already under natural load.
- Monitor: extend the [[project_playbook_shadow_tracking_820]] pattern to include IO burst balance if Supabase exposes it via API.
