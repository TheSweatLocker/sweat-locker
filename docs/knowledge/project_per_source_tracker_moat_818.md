---
name: per-source-tracker-moat-818
description: Rolling per-source (FR/CZ/OC) sharp-side calibration is a moat — must run continuously and surface transparently in-app
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-19T22:04:54.714Z
---

Rolling per-source sharp-signal calibration is a **moat** for The Sweat Locker and must be built as a continuous, transparent system — not a one-off audit.

**Why:** 8/18 investigation surfaced a stunning pattern in 7d MLB data (94 games): 3/3 source AGREEMENT hits 45%; the MINORITY DISSENT side hits ~63% across all three dissenters (FR/CZ/OC). Individually per source over 7d: FR 47.3%, CZ 52.5%, OC 43.9% — OC ML at 29% is a near-inverse signal. The user's Athletics-tonight eye test independently matched the OC-dissent side vs the FR+CZ majority (Royals). User: "this is something we need to track all the time it should be rolling, just being transparent to user with what we see that is the moat and it should be rolling updating in decision making process."

**8/20 update — 30d MLB confirmation (n=460+ picks graded):**
- FR 45.5% (n=77), CZ 51.9% (n=162), OC 48.9% (n=329) — all near/below breakeven
- 3_of_3_AGREE 50.0% (n=24) — coin flip, not a moat when everyone agrees
- **DISSENT_OC 77.3% (n=22, +24.9pp edge)** — when only OC dissents, OC is right 77%
- **MAJ_when_OC_dissents 22.7% (n=22, -29.7pp edge)** — following FR+CZ against OC LOSES 77%
- DISSENT_FR 61.5% (n=13), MAJ_when_FR_dissents 38.5%
- DISSENT_CZ 44.4% (n=18) — CZ dissent is noise
- Pattern is real on 30d for OC-dissent + FR-dissent buckets

**How to apply:**
- Sharp Card / Ensemble should treat OC-dissent as a HIGH-PRIORITY signal, not a downweight — flip pick to OC's side when it fires
- In-app "The Split" tab: surface `sharp_source_calibration` + `sharp_agreement_calibration` tables (migration 20260820) as track-record + MINORITY DISSENT badge
- Cross-sport: NFL/NCAAF have 0 snapshots today (pre-season). Framework is sport-universal — recompute automatically once games start
- Nightly refresh wired in mlb_pipeline.yml workflow after finals resolve
- Related: [[project_sharp_money_fade_808]] (root sharp-fade insight this extends)

**How to apply:**
- Every model rebuild / calibration cycle must recompute per-source hit rates (7d, 14d, 30d rolling) and per-agreement-bucket hit rates.
- Surface this rolling calibration in-app (transparency), not just in internal dashboards. Think: "Our sources: CZ 52% · FR 47% · OC 44% (7d)".
- Feed the rolling numbers back into classifier weighting — reduce weight on losing sources, upweight dissent-side when historical dissent buckets outperform consensus.
- 3/3 CONFIRMED tier language may be misleading given current data — validate at 30d before marketing leans on TRIPLE_CONFIRMED as the loudest tier.
- Related: [[project_sharp_money_fade_808]] — sharp $ = FADE was the original insight; this extends to per-source-and-agreement-bucket calibration.
