---
name: project_yday_814_variance_lesson
description: 2026-08-14 grade-card audit findings — tail-heavy variance night hid the fact that our high-conviction prop framework actually delivered to book. Key: refit-backed BACKs (refit>=70) went 4-2 (67%); losses were "Jerry high conv + refit thin" spots.
metadata:
  type: project
---

**2026-08-14 grade-card audit** — surface-level "everything lost" masked real signal.

**Aggregated hit rates:**
- Props BACK: 8-6 (57%)
- Props BACK conv >=70 with refit>=70: **4-0** (best cohort)
- Props BACK conv >=70 with refit<70: **0-2** (Tyrone Taylor 72/58.8, Ortiz 72/67)
- hits_over/over BACKs: 5-2 (71%) — beat historical 66% PRIME bucket
- OddsCrowd ML: 6-8 (43%)
- OddsCrowd Total: 5-9 (36%)
- Fadereport signals: 4-11 (27%) — n=15 small sample
- Primary play (pipeline): 2-9 (18%)
- Jerry MLB game reads: 1-11 (8%) plus 2 PASS

**Why: tail-heavy variance day.** 7 of 14 games hit tails > 4 runs vs line (50% of slate vs typical 25-30%). Extremes: SEA/HOU +9, MIA/CIN -7, ARI/ATL -6, CWS/DET +5.5. Half the slate broke tails — any total-heavy strategy would lose.

**Full confluence pattern (n=1)** — COL/SFG ML/Giants had pipeline + OC + Fadereport all pointing same side. Lost. Sample too small — track over 30+ slates before drawing conclusion. **Hypothesis to test: when ALL 3 sharp sources agree, base rate may be BELOW random.** If validated, becomes a fade signal.

**Where confluence disagreement was correct:**
- BOS/PIT: Pipeline said Bos ML PRIME; Fadereport said sharp Pirates +31pt. Pirates won 8-4. **Fadereport disagreement was right.**
- MIA/CIN: OC ML fade=boost signal on Marlins (which lost). Sharp $ fade was right.

**How to apply:**
1. **When conv >=70 BUT refit<70, treat as LEAN not PRIME.** The 2 losses on Tyrone Taylor + Abimelec Ortiz followed this pattern. Refit is the calibration check on Jerry — thin refit backing = downgrade.
2. **Props BACK are more resilient to game-variance nights than game-level plays** — per-hitter contact quality (L14 wRC+, L7 BA, wind, opp starter) is orthogonal to team-level scoring variance.
3. **Don't over-index on a single-day fadereport hit rate** — 27% at n=15 is not enough data to demote the source. Track over 30+ days before adjusting weighting.
4. **On tail-heavy nights** (>=6 of 14 games hitting >4 run tails), acknowledge in card writeup that game-level primary plays face elevated variance. Prop plays remain the more reliable exposure.
5. **The "everything lost" narrative is misleading** — high-conviction refit-backed BACKs delivered exactly to historical rate. What lost was mid-conviction plays + game-level totals.

Related: [[project_calibration_architecture_805]], [[feedback_confidence_in_first_pass]], [[project_batter_hits_signal_712]].
