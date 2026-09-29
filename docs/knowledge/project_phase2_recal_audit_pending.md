---
name: phase2-recal-audit-pending
description: Phase 2 book-line recal code shipped 5/29 evening but deferred to 5/30 cron. Audit baseline frozen at mlb_pipeline/audit_phase2_recal_dryrun_2026-05-29.json — compare against 5/30 fresh cron output to grade.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Phase 2 recalibration (HA / ER / Outs / BB) shipped to `mlb_pipeline/generate_props.py` on 2026-05-29 evening, mirroring the K-only Phase 1 recipe from 5/28. Key changes:
- Module-level maps: `PROP_MARKET_MAP`, `PROP_PROJ_KEY`, `EDGE_BANDS`, `LEAN_PROMOTION_RANGES`
- `fetch_book_lines_for_market(date_str, market)` — generalizes the K-only fetch
- `attach_book_lines(props)` — now attaches lines for K + BB + HA + Outs + ER
- `recalibrate_props_with_book_lines(props)` — generalized; K alias preserved
- `_projected_outs` / `_projected_er` signal-storage added to the outs/ER scorers
- `_keep()` retention now keeps SKIP'd recalibrated props for all 5 markets (not just K)

**Why not run live tonight:** user said "leave as is, pick up tomorrow." Avoided re-running generate_props.py against tonight's slate.

**How to apply tomorrow morning:**
1. The 6am ET cron picks up the new code organically — first production Phase 2 run lands then.
2. Audit baseline saved at [`mlb_pipeline/audit_phase2_recal_dryrun_2026-05-29.json`](mlb_pipeline/audit_phase2_recal_dryrun_2026-05-29.json) — 32 frozen props from tonight + 3 manual-audit cases user flagged live:
   - Meyer U HA: PRIME 91 (line 5.5) → STRONG 68 (book 4.5, edge +0.9)
   - Lorenzen O ER: PRIME 86 (line 2.5) → STRONG 77 (book 3.5, edge +0.5)
   - Rodón U HA: PRIME 80 (line 5.5) → LEAN 44 (book 4.5, edge +0.3)
3. Tomorrow's first task: pull 5/30 fresh-cron prop output, count tier shifts by market group (ks vs bb vs ha vs outs vs er), compare distribution shape vs Phase 1 K-only baseline. Confirm tiers are landing where intuition says they should before publishing.

**Dry-run blocker found 5/29:** Odds API only serves lines for *upcoming* games (`commenceTimeFrom=now_utc` filter). After tonight's first pitches, fetching book lines for tonight's pitchers returned tomorrow's slate instead. Means: dry-run audits against same-day evening props are impossible; audits must happen during the morning window before games start.

**Edge bands per prop group** (in EDGE_BANDS):
- ks/ha: 1.5 / 1.0 / 0.5 / 0.0 thresholds → 1.00 / 0.90 / 0.75 / 0.55 multipliers (below 0 → 0.30)
- outs: 3.0 / 2.0 / 1.0 / 0.0 (3-outs-per-inning scale)
- bb: 0.5 / 0.3 / 0.1 / 0.0 (half-walk matters)
- er: 1.0 / 0.5 / 0.3 / 0.0 (ER is tighter band)

**LEAN promotion ranges** (`LEAN_PROMOTION_RANGES`):
- ks/outs/er: 55-69 (PRIME 82 / STRONG 70 cutoffs)
- bb/ha: 40-54 (PRIME 70 / STRONG 55 cutoffs)

Related: [[k-over-audit-cohorts]] (Phase 1 recipe origin), [[may29-docket]] (queue context).
