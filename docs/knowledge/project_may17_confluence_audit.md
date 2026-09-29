---
name: may-17-confluence-cohort-audit
description: "PRIME confluence (≥4) dropped from 68.8% → 61.1% STD after 5/16's 0-4 day. 7-day window at 43.8% is a yellow flag. Back off PRIME-tier sizing on standalone confluence plays."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## Headline

`confluence_prime_ge4` cohort moved from **22-10 (68.8%) STD** before 5/16 → **22-14 (61.1%) STD** after yesterday's 0-4 day on PRIME-confluence ML side picks (PIT, SEA, TB, TEX all lost).

7-day rolling: **7-9 (43.8%)** — material yellow flag for recent reliability.

## Statistical reality check

- True 68.8% cohort going 0-4 has ~1% probability — yesterday was extreme variance
- BUT n=36 lifetime is small; true rate could realistically be 55-70%
- Single bad day shouldn't trigger a retune; pattern would need to persist

## What changed in practice

**Before:** PRIME +4 confluence games could anchor a card on their own (treated as 68% lock).
**Now:** PRIME +4 needs a second-signal pairing (mastery + opp-form, xERA gap + bullpen, etc.) before card-grade conviction. Standalone = STRONG sizing, not PRIME.

## Today's affected cards

- **BOS/ATL PRIME +4 home** — back at STRONG sizing; carrying Bello anti-mastery as second-signal pairing makes it card-grade
- **PHI/PIT PRIME +4 home** — Skenes mastery vs PHI (15.7 IP / 0.57 ERA / .098 BAA) is the second signal that justifies card placement
- **LAD/LAA PRIME +4 away** — only PRIME confluence game without a second-signal anchor → skip

## Audit cohort ranking (post-update)

| Cohort | STD % | n | Notes |
|---|---|---|---|
| `k_over_with_total_under` | 75.7% | 37 | **strongest** |
| `nrfi_prime_90_94` | 72.4% | 29 | today's POTD |
| `k_under_strong` | 64.3% | 28 | |
| `confluence_extreme_ge6` | 63.6% | 11 | small sample |
| `confluence_prime_ge4` | **61.1%** | 36 | degraded from 68.8 |
| `total_over_bp_era_high` | 58.3% | 72 | large sample modest edge |

## Followup items

- **Monitor next 7-14 days** — if `confluence_prime_ge4` 7-day stays <50%, structural shift not variance
- **Pair-gate idea:** require `(confluence_net ≥4) AND (mastery_or_1st_inn_extreme_or_prop_stack)` for card-grade. Would have correctly held BOS/ATL + PHI/PIT today, skipped LAD/LAA.
- **Watch `confluence_extreme_ge6` (+6+):** still 63.6% but only n=11. If we see one this week, treat as PRIME-grade.
- **`nrfi_prime_90_94` is the strongest single cohort.** When sweet-spot band is available, POTD-lock confidence is justified.

## Related
- [[project_may15_calibration_notes]] — same 5/17 audit docket
- [[project_dod_confluence_bug]] — fixed the DOD direction issue that was upstream of these confluence reads
- [[project_sweat_score_rewrite]] — sweat score now weights confluence into game scoring
