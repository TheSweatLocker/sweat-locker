---
name: project-picks-engine-walkthrough-908
description: "🎯 9/8 queued: user feels lost in the sauce on how picks are made. Walk through LR vs ensemble vs shadow vs override logic. Explain why LR has been winning lately."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T18:17:25.094Z
---

**User directive · 9/8 evening:** "Queue up discussion how picks are being made now feel like I am lost in the sauce, also want to know exactly what goes into LR? Why is LR successful lately? Difference between LR and models?"

## Why this needs a real walkthrough

The pick engine has grown organically: legacy ensemble → adaptive weighting → LR shadow → LR override → refit overlay → juice gate → LR confidence gate (POTD Option A). Even I have to trace it every time. The user needs a clean mental model.

## Topics to cover in the walkthrough

### 1. What is LR (Logistic Regression)?
- A **single model** trained per sport-market (MLB ML, MLB total, NCAAF total, NFL total, etc.)
- Features are the signals that historically predicted the outcome (pitcher stuff, rest days, park factor, etc.)
- Output = single probability (`p_home_win` or `p_over`)
- **Deterministic** — same inputs → same output
- **Auditable** — coefficient table shows exactly which signals matter and by how much

### 2. What is "the ensemble"?
- The legacy engine — a **weighted signal stack** (not a real ML model, more like a scoring rubric)
- Signals from cohorts, external picks, matchup stats, pitcher form, etc. all get a "contribution" score
- Higher score = higher pipeline conviction
- Weights hand-tuned, calibrated periodically from graded history

### 3. Where do they interact?
- Ensemble picks first → LR runs in shadow → if LR STRONGLY disagrees, `defensive_gates.apply_mlb_lr_override` demotes conviction (COVERAGE) OR flips side
- On today's TOR@ATH, ensemble said Athletics ML PRIME conv 89 → LR said 0.49 (coin flip) → LR demoted to COVERAGE with audit note "MLB LR sees coin flip"
- Currently: LR can DEMOTE but ensemble still picks the side. So POTD selection needs its own LR gate (shipped tonight in jerry_anchor_potd)

### 4. Why has LR been successful lately?
- LR is trained on **actual outcomes**, not analyst intuition
- Ensemble weights drift when signal calibration is stale (fade rules from 3 months ago may not apply today)
- LR retrained periodically absorbs regime shift better
- LR is honest about coin flips — it says 0.52, not "PRIME conv 89"
- Sharp Card LR shadow gate has been dropping picks where LR STRONGLY disagreed → hit rate improved

### 5. What's different between LR and "the models"?
- MLB has ~4 models: runs_model_v4, panel projection, ensemble_v2, LR shadow
- **LR is the only pure ML classifier** — the others are physics-inspired simulators or weighted rubrics
- LR handles the "is this pick actually a good bet" question
- The other models handle "what will this game look like" (runs scored, hit rate, etc.)

### 6. The full pick flow (2026-09-09 state)
```
1. Data ingestion (odds, weather, lineup, pitcher stats)
2. Sim models score the raw game (runs, hits, win prob per side)
3. Signal stack scores each market (ML/RL/Total) → picks side + tier
4. Ensemble_v2 combines signal weights → primary_play with conviction 0-100
5. LR runs in shadow → writes to primary_play._lr_ml_shadow / _lr_total_shadow
6. LR override (defensive_gates) → demotes conviction if LR strongly disagrees
7. Jerry synthesis writes narrative to jerry_reads.short_read / long_read
8. Sharp Card composer applies LR-shadow-conflict gate → drops picks where LR flips
9. POTD selector applies LR ≥ 0.60 gate → skips coin flips
10. UI renders whatever survived
```

### 7. Where the confusion comes from
- Different surfaces apply DIFFERENT gates in different orders
- primary_play can show ensemble side, LR can silently override, jerry_reads shows both
- "PRIME conv 89 LR-demoted to COVERAGE" is a real thing that shows up as PRIME to some queries, COVERAGE to others depending on which field they read

### 8. What we should DO with this walkthrough
- Diagram of the flow above (visual, in-app or in docs)
- "How Jerry decides" explainer inside Steam Room
- Or a Receipts-style page: "The 5 gates a POTD passes through"
- Even internal — a doc I can reference so my own summaries stop being all-over-the-place

## Related memory
- [[project_data_infrastructure_priorities_908]] — LR is Priority 1
- [[project_lr_totals_investigation_908]] — LR cross-sport audit
- [[project_potd_selection_redesign_908]] — Option A shipped, discussion queued
- [[project_signal_framework_821]] — comprehensive signal checklist
- [[project_adaptive_model_ensemble_802]] — ensemble weighting explained
