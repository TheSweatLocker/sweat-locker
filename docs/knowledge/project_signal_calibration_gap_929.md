---
name: project_signal_calibration_gap_929
description: "🚨 Per-signal football audit. signal_registry weights on COHORT hit rate, which diverges from pick-level ATS by up to 19pp in BOTH directions. 7 of 19 firing signals have no registry entry. No individual signal change clears 2 SE yet."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-29T15:58:44.537Z
---

Measured 2026-09-29 on 317 graded football side picks (NFL wk1-3, NCAAF thru
wk4). 84 distinct signal_keys fired; 19 fired at n>=15. This is the layer below
[[project_football_engine_audit_929]] and it is where the conviction inversion
actually originates.

## The mechanism behind the class-count inversion

The signals that fire MOST OFTEN are the mediocre ones:

| signal | ATS | n | registry weight |
|---|---|---|---|
| `ncaaf_ol_weight_adv_home` | 47.5% | 99 | **1.0** |
| `ncaaf_ol_weight_adv_away__fade` | 50.6% | 87 | none |
| `ncaaf_ground_leverage_away__fade` | 48.7% | 78 | none |
| `ncaaf_ground_leverage_home` | 45.1% | 51 | **1.0** |

Those four fire 315 times at a combined 48.3%. A pick backed by many signals is
therefore dominated by high-frequency mediocre ones, while a pick backed by 1-2
is more likely carried by a genuinely good one. That is why <=2 classes hits
60.8% and 5+ hits 53.0%.

## Signals that ARE predictive (clear 2 SE)

- `ncaaf_confluence_home` 82.4% n=17 **z=+2.47** — already weight 1.0
- `ncaaf_home_field_baseline` 70.5% n=44 **z=+2.40** — already weight 1.0

Both correctly weighted. Nothing to change there.

## THE REAL FINDING: registry measures a different population

`signal_registry.hit_rate` is the COHORT rate (all games where the condition
held). What matters is performance WHEN THE SIGNAL DRIVES A PUBLISHED PICK.
Those diverge badly, in both directions:

| signal | registry claims | actually delivers | gap | weight |
|---|---|---|---|---|
| `ncaaf_ground_leverage_home` | 55.8% | 45.1% | **+10.7** | 1.0 |
| `ncaaf_home_field_baseline` | 79.6% | 70.5% | +9.1 | 1.0 |
| `ncaaf_ol_weight_adv_home` | 56.4% | 47.5% | **+8.9** | 1.0 |
| `home_team_ats_hot_season` | 46.2% | **65.2%** | **-19.0** | **0.0** |
| `external:covers` | 44.4% | **62.5%** | **-18.1** | **0.0** |

Mean overstatement across the 10 with entries is only +0.3pp, so the registry is
not uniformly broken — but the per-signal error is large, and the two most
UNDER-stated signals are weighted 0.0. The engine is ignoring two of its better
performers because a cohort statistic says they are coin flips.

## 7 of 19 firing signals have NO registry entry at all

`away_ats_hot_on_road__fade` (n=40), `ncaaf_ol_weight_adv_away__fade` (n=87),
`ncaaf_ground_leverage_away__fade` (n=78), `home_ats_cold_at_home__fade` (n=18),
`home_team_ats_cold_season__fade` (n=34), `away_covers_as_dog__fade` (n=30),
`ncaaf_sp_plus_edge_away_rl__fade` (n=22).

Every one is a `__fade` variant. The fade inversions fire and contribute but are
never calibrated — 315 firings of uncalibrated signal in one season.

## WHY NOTHING ELSE WAS CHANGED

Neither bad signal clears 2 SE individually:
- `ncaaf_ol_weight_adv_home` 47.5% n=99 → **z=-0.98**
- `ncaaf_ground_leverage_home` 45.1% n=51 → **z=-1.04**
- all four OL/ground combined 48.3% n=315 → **z=-1.46**

Suppressing or reweighting on that would be the same unmeasured adjustment Andy
stopped on the postseason prop cap. Recorded, not acted on.

## How to apply

**Act when** any of these crosses z=-2 (roughly: `ol_weight_adv_home` needs
~46% at n=150, `ground_leverage_home` ~45% at n=100). Re-measure after NFL wk 6 /
NCAAF wk 7.

**Structural fixes that need no new evidence:**
1. Give every `__fade` variant a registry entry. Uncalibrated signals should not
   contribute weight.
2. Make the registry track PICK-LEVEL hit rate alongside cohort hit rate, and
   weight on the former. They are different questions and the ensemble is using
   the wrong one.

Related: [[project_football_engine_audit_929]] (totals barred, classes_boost
zeroed), [[project_fade_gate_performance_921]] (SHARP_MOVE no edge — same
family), [[project_ncaaf_dog_bias_926]].
