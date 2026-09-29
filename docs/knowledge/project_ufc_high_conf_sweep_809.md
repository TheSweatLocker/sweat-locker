---
name: ufc-high-conf-sweep-809
description: Two UFC fight nights in a row (approx 8/2 + 8/8) where high-conviction model predictions swept per user report. UFC picks not yet graded in DB — grading pipeline is a gap.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T12:26:11.818Z
---

**2026-08-09 user report**: The UFC model has now had **two fight nights in a row where high-confidence PRIME predictions all cashed**. Working from memory:
- 8/2 UFC Fight Night: high-conf PRIME sweep
- 8/8 UFC Fight Night (Gamrot vs Salkilld): 3 PRIME picks — Salkilld ML, Nurgozhay ML, Thainara ML — all reported as wins

**Confirmed picks for 8/8** (in ufc_picks table):
- Salkilld ML (main event, PRIME +54 EV, model 88.8%)
- Nurgozhay ML (PRIME +37 EV, model 85.4%)
- Thainara ML (PRIME +24 EV, model 90.3%)

**Gap identified**: `ufc_picks` table has no `result` / `winner_actual` / `graded_at` columns. UFC picks aren't being auto-graded. Need to add:
1. `winner_actual TEXT`, `method_actual TEXT`, `rounds_actual INT`, `graded_at TIMESTAMPTZ` columns
2. Grading script pulling from ESPN or Sherdog after events end
3. Cross-reference to `ufc_espn_enrich` for post-event stat updates

**How to apply**: When UFC results discussed, don't quote from DB (not there yet) — trust user's real-time reports until UFC grader ships. Post-fight card, prioritize building the grader so we can compute real UFC hit rates (currently only anecdotal).

**Sharp money for UFC**: Not tracked. OddsCrowd scraper supports MMA slug but we don't pull it. Adding UFC sharp money would let sharp_fade_audit_trail cover UFC too (schema already sport-agnostic).

**Follow-ups queued**:
- UFC grader (pull results from ESPN post-event, backfill winner + method)
- UFC OddsCrowd puller (pull_externals_ufc.py mirror)
- Once graded, cross-sport `sharp_fade_audit_trail` becomes real

Related: [[project_ufc_sprint_729]], [[project_ufc_pipeline_broken_726]].
