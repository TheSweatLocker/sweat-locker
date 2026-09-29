---
name: project_all_sports_engine_929
description: All-sports engine assessment 2026-09-29. Combined 297-225 (56.9%) z=+2.06. MLB tier ordering is CORRECT; NCAAF is badly inverted (PRIME 33% n=15, conv 80+ 41.5% vs 50-59 57.8%). NBA/NCAAB have zero graded picks.
metadata:
  type: project
---

Every sport, every graded primary_play, 2026 season. Joined via the new
results_game_id for football, game_id elsewhere.

| sport | graded | record | hit% | z | market mix |
|---|---|---|---|---|---|
| MLB | 147 | 86-61 | 58.5% | +1.48 | ml 76, total 69, rl 2 |
| NFL | 48 | 29-19 | 60.4% | +1.11 | ml 25, rl 17, total 6 |
| NCAAF | 276 | 149-127 | 54.0% | +0.53 | rl 180, ml 59, total 37 |
| NHL | 51 | 33-18 | 64.7% | +1.76 | ml 51 (only) |
| NBA | **0** | — | — | — | nothing graded |
| NCAAB | **0** | — | — | — | nothing graded |

**COMBINED: 297-225 (56.9%) n=522 z=+2.06** — the engine overall is genuinely
profitable and statistically real.

## THE INVERSION IS NCAAF-SPECIFIC — this corrects an earlier claim

I previously said "conviction is inverted" from a football-pooled measurement.
Split by sport it is much more precise:

    MLB    PRIME 63% (n=97)  STRONG 58% (n=19)  COVERAGE 47% (n=30)   ORDERED ✓
    NFL    PRIME 58% (n=12)  STRONG 69% (n=16)  COVERAGE 53% (n=15)   top inverted
    NCAAF  PRIME 33% (n=15)  STRONG 58% (n=72)  LEAN 54% (n=96)
                                                COVERAGE 55% (n=69)   BADLY inverted
    NHL    STRONG only (n=51) 65%

    conviction 80+ vs 50-59:  NCAAF 41.5% (n=53) vs 57.8% (n=116)  INVERTED

**MLB's tier ladder works.** NCAAF's is broken — PRIME is its WORST tier and the
80+ conviction bucket underperforms the 50-59 bucket by 16pp on healthy samples.
Whatever is wrong with conviction is not engine-wide; it is concentrated in NCAAF,
which is also the sport with the most signals firing per pick.

## Other observations

- NHL emits ONLY moneyline picks (51/51). No spread or total picks at all — worth
  knowing before trusting an NHL "all markets" claim.
- MLB is ml+total almost exclusively (76+69 of 147), only 2 spread picks. MLB
  totals are 69 picks and were NOT barred (the totals bar is football-only) —
  given sharp-money fade is strongest on MLB totals
  ([[project_sharp_money_is_a_fade_929]]), MLB total performance deserves its own
  study.
- NBA and NCAAB have ZERO graded picks. NBA opens 10/21 for real games; NCAAB
  11/03. Nothing about either engine has ever been validated against a result.

## How to apply

1. Do NOT apply football conviction fixes to MLB — its ladder is correctly
   ordered and changing it would break something that works.
2. NCAAF conviction is the priority target. It is also where classes_boost had
   the most effect (NCAAF picks carry 3+ source classes; NFL's mostly do not).
3. Treat NBA/NCAAB picks as unvalidated until graded games exist.

Related: [[project_football_engine_audit_929]],
[[project_signal_calibration_gap_929]], [[project_sharp_money_is_a_fade_929]].
