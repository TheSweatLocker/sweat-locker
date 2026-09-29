---
name: ha-under-conviction-band-720
description: "30d HA_UNDER audit: conviction 85+ hits 66.7% n=21 ⭐, but 75-84 band is 45.8% n=24 (WORSE than coinflip). Card discipline should distinguish PRIME 85+ from PRIME 75-84."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  audit_date: 2026-07-20
  modified: 2026-07-20T18:52:20.725Z
---

**Set 2026-07-20 after 30d hits-allowed audit.**

## The core finding

HA_UNDER 30d overall: **71-60 = 54.2% (n=131)** — profitable surface.

**BUT the conviction band matters more than the tier label:**

| Conviction | Record | Hit% | Sample |
|---|---|---|---|
| **85+** | 14-7 | **66.7%** ⭐ | n=21 |
| 75-84 | 11-13 | 45.8% ⚠️ | n=24 |
| 65-74 | 14-15 | 48.3% | n=29 |
| 55-64 | 17-16 | 51.5% | n=33 |
| <55 | 15-9 | 62.5% | n=24 |

**The 75-84 band is a trap zone.** Below coinflip despite carrying the PRIME/STRONG label.

## Why this matters

7/19 recommended card had 2 HA_UNDERS both in the trap zone:
- Foster Griffin PRIME **79** — U 6.5 → lost 8H
- Trey Yesavage PRIME **77** — U 4.5 → lost 5H

And I FADED the one in the winning band:
- Eury Pérez PRIME **86** — U 4.5 → won (1H) ← 85+ band = 66.7% historical

Structurally the exact wrong picks selected from the pipeline.

## Tier vs conviction breakdown (30d)

| Tier | Record | Hit% | n |
|---|---|---|---|
| PRIME | 29-24 | 54.7% | 53 |
| STRONG | 3-4 | 42.9% | 7 |
| LEAN | 12-5 | 70.6% | 17 |
| SKIP | 27-27 | 50.0% | 54 |

PRIME label alone is only ~55%. LEAN is 70.6% in a small sample. Tier label is a weak sort — **conviction band is the real signal**.

## Retrospective margin distribution (HA_UNDER)

- Won by 3+: 12.2%
- Won by 2-3: 5.3%
- Won by 1-2: 14.5%
- Won by 0.5-1: 22.1% ← biggest win cluster
- **Lost by <0.5: 17.6%** ← razor-thin losses (biggest single loss cluster)
- Lost by 0.5-1.5: 10.7%
- Lost by 1.5-3: 9.9%
- Lost by 3+: 7.6%

**28.3% of HA_UNDER bets lose by <1.5 hits.** "Cushion looks generous" is a mirage in that range.

## Card selection rules going forward

1. **HA_UNDER: only publish 85+ conviction** (66.7% hit rate)
2. Treat 75-84 as SKIP for card purposes — call it out separately as "watch list" not "play"
3. Under 3-hit projected cushion @ juice ≥ −150 → automatic pass (razor-thin loss cluster + juice ate breakeven)
4. Ignore tier label alone; sort on conviction band

## HA_OVER for reference

- Overall 45.3% (n=75) — do NOT publish HA_OVERs without additional signal
- PRIME HA_OVER 56.2% (n=16) — thin sample, still weak
- LEAN HA_OVER 25.0% (n=16) — actively losing

## Blocker for calibration audit

`signals` JSON field on `mlb_pipeline_props` doesn't consistently store `projection` value. Can't do proj-vs-actual calibration until scorer adds it. **Ticket:** add `projection` to signals payload on write.

## Related

- [[project_hits_allowed_calibration_719.md]] — original 7/19 signal
- [[feedback_card_process_discipline_718]] — juice check rule 6 aligns
- [[feedback_dont_fade_prime_on_pattern_alone]] — Pérez fade was wrong; this audit confirms 85+ band should have been trusted
- [[project_prop_tier_x_type_615]] — earlier tier×type audit found variance by prop type
