---
name: 30d-model-audit-723
description: "30d n=364 audit shows Panel king on favs (65.8%), Jerry only lens >50% on dogs (52%), v4 sides = fav-only specialist, v4 total = noise, v3 UNDER specialist (63% n=38 small)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-24T00:27:27.583Z
---

**Set 2026-07-23 evening. n=364 graded MLB games since 2026-06-23.**

## Sides results

| Model | Overall | Favs | Dogs |
|---|---|---|---|
| Panel | 59.7% (n=238) | **65.8%** (n=155) | 48.2% (n=83) |
| Jerry | 57.6% (n=309) | 60.4% (n=207) | **52.0%** (n=102) |
| v3    | 57.2% (n=285) | 60.8% (n=212) | 46.6% (n=73) |
| v4    | 53.2% (n=295) | 63.1% (n=130) | 45.5% (n=165) |

## Totals results

| Model | Overall | Overs | Unders |
|---|---|---|---|
| Jerry | 53.3% (n=274) | 53.2% (n=186) | 53.4% (n=88) |
| v3    | 52.6% (n=251) | 50.7% (n=213) | **63.2%** (n=38) |
| Panel | 51.5% (n=237) | 55.1% (n=78) | 49.7% (n=159) |
| v4    | 47.3% (n=281) | 47.4% (n=247) | 47.1% (n=34) |

## Key rules going forward

1. **Panel is the sides anchor on favs.** Highest single-lens hit rate.
   Composite panel-variant weight (0.4) is correct.
2. **Jerry is the ONLY dog-friendly lens (52%).** When picking dogs,
   shift composite weight toward jerry. Queue: bucket-conditional
   composite spread — dog picks use jerry=0.4 / panel=0.2 / v3=0.3 /
   v4=0.1 instead of the current fav-tuned weights.
3. **v4 sides = fav specialist only.** 63.1% on favs, 45.5% on dogs.
   Consider gating v4 to zero weight on dog picks in composite.
4. **v3 UNDER specialty (63.2% n=38).** Small sample but loud —
   investigate what triggers v3 loud UNDER, might be a cohort.
5. **v4 total = noise.** 47.3%. Current 0-weight from ace/pitcher/
   hitter park buckets is correct.

## Related

- [[project_mc_v2_backtest_723]] — MC v2 rich hits 59.4% sides (comparable to panel)
- [[project_morning_audit_723]] — 7/22 evening night-of confirmation
- [[project_model_reweight_721]] — 60d reweight audit
