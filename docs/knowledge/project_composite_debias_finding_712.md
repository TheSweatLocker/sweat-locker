---
name: composite-debias-finding-july-2026
description: "30d backtest shows jerry debiased (58.5% raw, 60.5% debiased) beats composite (52.8%) by +7.8pp. Panel is coinflip over full sample."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Discovered 2026-07-12 during fix-round K.** 30-day rolling backtest of total prediction over n=190 aligned games (all 4 lenses populated: v3, v4, jerry, Panel).

## Ranked hit rates (aligned n=190, jerry avg drift vs line +0.612)

| Config | W-L | n | Hit % | Δ baseline |
|---|---|---|---|---|
| **jerry debiased (jerry − 0.612)** | 89-58 | 147 | **60.5%** | +7.8pp ⭐ |
| jerry only | 86-61 | 147 | 58.5% | +5.7pp |
| panel 40% + v3 30% + jerry-debias 30% | 72-54 | 126 | 57.1% | +4.4pp |
| composite (jerry debiased) | 75-57 | 132 | 56.8% | +4.0pp |
| panel 40% + v3v4 25/25 + jerry 10 | 76-59 | 135 | 56.3% | +3.5pp |
| panel + jerry-debias (equal) | 82-65 | 147 | 55.8% | +3.0pp |
| panel 50% + v3v4 25/25 | 73-61 | 134 | 54.5% | +1.7pp |
| median(v3, v4, jerry, panel) | 76-65 | 141 | 53.9% | +1.1pp |
| **composite (v3+v4+jerry) BASELINE** | 76-68 | 144 | **52.8%** | 0.0pp |
| v3 only | 70-63 | 133 | 52.6% | -0.1pp |
| composite v3+v4 only | 83-77 | 160 | 51.9% | -0.9pp |
| v4 only | 80-81 | 161 | 49.7% | -3.1pp |
| **panel only** | 75-77 | 152 | **49.3%** | -3.4pp |

## Key findings

1. **Jerry debiased is the champion** — subtract +0.612 from jerry projection, use alone → 60.5%.
2. **Panel is a coinflip over 30d** — the recent 5-day 70% hot streak was tail variance, not persistent alpha.
3. **v4 is broken** at 49.7% — needs its own retune investigation. Excluded from strongest configs.
4. **Composite drags jerry's edge down** because v4 + Panel add noise, not signal.

## How to apply

- **Immediate (card recs):** Use jerry-debiased as PRIMARY total lens. Panel becomes tie-breaker not primary. Do NOT weight Panel > jerry in card writeups going forward.
- **Medium-term (code):** Replace `composite = mean(v3, v4, jerry)` with `mean(v3, jerry - 0.612)` OR the weighted `0.4*panel + 0.3*v3 + 0.3*(jerry-0.612)`. Ship as v1.1 signal after 30d additional validation.
- **Do NOT rewrite the tier gate yet** — 2026-07-12 gate patch already flips to Panel on disagreements. That works structurally. The composite input into the gate is what needs reweighting.

## Sample-size caveats

- 30d n=190 → 95% CI at 60% hit rate is ±8pp. True range 52-68%.
- Composite baseline sample is n=144. Difference (52.8 vs 60.5) still statistically real but confidence-interval overlap at ~55% is possible.
- Aligned dataset requires all lenses populated → biased toward higher-profile matchups where all projections got computed.

## Related

- [[project_re_weight_model_votes_609]] — 6/9 jerry tot bands halved, precursor to this
- [[project_v4_over_drift]] — 5/24 v4 OVER-side calibration drift finding
- [[project_v4_blackout_606]] — v4 guard too aggressive, related v4 concerns
- [[project_confluence_dead_signal_712]] — same-day audit of confluence net (dead)
