---
name: project-dissent-audit-822
description: "8/22 cross-source dissent audit — MAJ_when_CZ_dissents +16pp winner, 3_of_3_AGREE -11pp COUNTER-INTUITIVE FADE, MAJ_when_OC_dissents -20pp fade"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-22T18:27:17.252Z
---

**8/22 sharp-source dissent audit** — queried `sharp_source_calibration` (per-source per-market hit rate) + `sharp_agreement_calibration` (agreement-bucket hit rate) lifetime + 30d + 7d.

## Winning patterns (BACK signals, n≥15)

| Bucket | Market | n | Hit% | Edge (pp) | Action |
|---|---|---|---|---|---|
| MAJ_when_CZ_dissents | ALL | 19 | 68.4% | **+16.02** | 🚨 NEW — implement auto-flip mirroring DISSENT_OC |
| DISSENT_OC | ALL | 28 | 67.9% | **+15.46** | ✅ Already wired (Option B 8/22, `game_context.py:3040`) |
| 2_of_2_AGREE | total | 25 | 60.0% | +7.60 | Consider upgrading badge for totals-only |

## Losing patterns (FADE signals — take the OTHER side, n≥20)

| Bucket | n | Hit% | Edge (pp) | Interpretation |
|---|---|---|---|---|
| MAJ_when_OC_dissents | 28 | **32.1%** | **-20.26** | Mirror of DISSENT_OC (67.9% × inversion). Already covered by DISSENT_OC auto-flip. |
| **3_of_3_AGREE** | 29 | **41.4%** | **-11.02** | 🚨 **COUNTER-INTUITIVE** — "SHARP TRIPLE 🔥" badge users see as MOST confident is historically a losing bet 58.6% of the time |

## The 3-of-3 problem

**Steam Room's "SHARP TRIPLE 🔥" badge is contradicted by history.** The badge implies "all three sources agree, high confidence." Data says: when all three agree, the majority side loses 58.6% of the time. Interpretations:

1. **Sample size caveat** — n=29 is small; 41.4% hit rate has ±9pp confidence band. Could plausibly be true 50% with variance.
2. **Selection bias** — 3-source agreement only fires when all three sources have data. Late-line moves or thin markets may skew who's captured.
3. **Real pattern** — 3-source agreement = maximum public visibility → line is already fully priced in, no edge left. Would explain why 2-of-3 has better returns (dissent = information asymmetry).

## Recommended actions (in priority order)

**P0 — MAJ_when_CZ_dissents auto-flip (mirror of DISSENT_OC)**
Add branch in `game_context.py` at ~line 3040 (same location as OC-dissent flip): when FR + OC agree AND CZ takes opposite side with money% ≥60, flip ML side toward CZ's dissent, downgrade tier, preserve original in `_cz_flipped` audit dict. Same shape as `_oc_flipped`. Expected edge: +16pp on ~19 samples/year.

**P1 — Steam Room badge review**
Do NOT auto-invert the TRIPLE badge (user-visible, brand-critical). Instead:
- Add a small "tracker" chip on TRIPLE cards showing live hit rate ("30d: 41% — small sample")
- Let users decide whether to fade
- Re-audit at n≥50 before considering programmatic action

**P2 — 2_of_2_AGREE totals boost**
Totals-only agreement at +7.60pp (n=25). Add signal_source row that boosts BACK conviction on total picks when only 2 sources exist AND they agree.

## How to apply

- Any FR/CZ/OC signal handling in `ensemble_scorer.py`, `game_context.py`, or new signal_sources rows must reference these lifetime edges rather than assumptions.
- Numbers auto-refresh via `audit_sharp_source_calibration.py` in `mlb_pipeline.yml` — safe to re-query monthly.

## Related

[[project_sharp_money_fade_808]] · [[project_per_source_tracker_moat_818]] · [[project_pipeline_audit_822]]
