---
name: project-ufc-model-broken-817
description: "UFC calibration LIVE 8/21: isotonic v1 (n=53) wired end-to-end. Flattens raw probs toward observed ~60%. Next: verify tier drop on next pipeline run, un-suspend."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-22T01:20:09.982Z
---

**8/21 UPDATE — recal LIVE:**
- `models/ufc_calibrator_v1.pkl` fit + saved (isotonic regression, n=53 graded from `ufc_picks`)
- Wiring at `ufc_predict.py:156-172` — auto-loads pkl, applies to `p_winner_a`
- `ufc_compute_ev.py:149` reads calibrated prob → EV → tier (no code change needed there; consumption is transparent)
- Fit script fixed: was pulling from `jerry_reads.input_snapshot` (wrong shape, 0 pairs); now pulls from `ufc_picks` directly

**Calibrator behavior (raw → cal):**
- 0.20 → 0.50 (+30pp)  ← heavy A-dog model prob → coin-flip observed
- 0.50 → 0.59 (+9pp)
- 0.65 → 0.64
- 0.80 → 0.65 (-15pp)  ← model confidence heavily damped
- 0.90 → 0.95

Effect: expect dramatic PRIME → SKIP/LEAN reallocation on next pipeline run because EV computed against damped probs won't clear the +8 gate as often.

**Still pending:**
1. Run next UFC pipeline cycle + verify tier distribution actually shifts
2. Un-suspend UFC picks in `sport_registry` (currently ladder_eligible=False + state_message) once distribution proven
3. Increase n from 53 → 100+ as more fights grade; rerun `python ufc_calibration_fit.py` weekly to refit

**Sample-size caveat:** n=53 is below the 100+ target the fit script itself warns about. Calibrator maps aggressively; edges may whipsaw. Monitor first week live.

**Historical (8/20 backtest before recal):**
- PRIME 5-5 (50%, -2.80u) — hit rate breakeven, juice bleed
- STRONG 0-1 · LEAN 1-0 · SKIP 4-8
- Overall 10-14, 41.7%, -8.39u across 24 graded picks

**Sharp-fade experiment (8/17)** at `ufc_compute_ev.py:169-187` still gated behind `UFC_SHARP_FADE_MODE` env flag. Was the coarse safety-net. Calibration is now the real fix. Comment inline still references it as "safety-net experiment" — accurate.

**Related:** [[project_ufc_sprint_729]] (original setup), [[feedback_batter_hits_juice_trap_803]] (same juice principle applied to MLB).
