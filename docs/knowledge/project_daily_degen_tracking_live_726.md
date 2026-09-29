---
name: daily-degen-tracking-live-726
description: "Daily Degen result tracking shipped 7/26 — migration + resolver + workflow + app surface. Historical backfill (92 parlays): 7-81 parlay (8.0%), 190-148 legs (56.2%). Currently -EV. Legs% gap of ~6pt to +EV target = the real focus."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-27T01:35:31.648Z
---

**Set 2026-07-26 EOD after Daily Degen tracking capability shipped end-to-end.**

## What went live

- **Migration** `20260726_daily_degen_results.sql` — adds `legs_resolved`
  JSONB + `result` (Win|Loss|Push|Pending) + `resolved_at`
- **Resolver** `mlb_pipeline/resolve_daily_degen.py` — supports PROP /
  ML / RL / NRFI / TOTAL leg types; parlay math is all-or-nothing with
  Push-neutrality
- **Workflow** — wired into `mlb_pipeline.yml` after resolve_props +
  resolve_game_results (continue-on-error true)
- **App surface** — Daily Degen tab shows track-record card between
  narrative + legs. Parlay W-L + hit rate + per-leg accuracy (Lifetime
  + Last 30D). Educational footer explains parlay math.

## Historical numbers (full 92-parlay backfill 7/26)

| Metric | Value | Reference |
|---|---|---|
| Parlay lifetime | **7-81 (8.0%)** | 4-leg parlay break-even ≈ 10-15% depending on avg juice |
| Legs lifetime | **190-148 (56.2%)** | +EV target ≈ 62% per leg at avg -110 |
| Pending | 3 | Awaiting props resolver catch-up |

**Daily Degen is currently -EV.** Legs at 56.2% need to be at ~62% for the
parlay format to print +EV. Gap = 6 percentage points.

## Why this matters

Two-lane read: the model's *picks* are OK (56% legs is real signal, not
noise), but the *product format* is math-eating the edge. Options:

- **Cut leg count** — 3-leg parlay at 56% = 17.6% hit rate; break-even
  at avg -110 ≈ 15% → close to +EV
- **Tighten leg selection** — audit which cohorts are dragging (early
  session found SKIP outs_under 100%-cohort was polluting selection;
  after cleanup, real cohort rates rank differently)
- **Reframe as "3 of 4 hit" content** — post-mortem numbers are honest
  about the format even when the parlay lost

## Related fixes shipped same session

- 312 corrupt pitcher-prop rows nulled (final_value=0 poisoning live
  30d cohort rates) — daily_degen selection now uses clean cohort data
- Haiku narrative timeout 10s → 25s + 1 retry (killed the "Model found
  edges across the slate" boilerplate fallback problem)

## Followup queue

- **Cross-sport blend** — currently MLB-only. Blocked on UFC pipeline
  rebuild ([[project_ufc_pipeline_broken_726]]). NBA/NFL/NCAAB/NCAAF
  extensions need their own pipeline_props tables to be uniform first.
- **Leg-count experiment** — a/b 4-leg vs 3-leg to see if legs% × leg
  count math works better shorter
- **Cohort audit inside DD extraction** — after 312-row cleanup, some
  cohort rates flipped. Verify no other 100%/0% mirror bugs.

## Related

- [[feedback_full_slate_artifact_format]]
- [[project_potd_universal_pool_720]]
