---
name: confluence-net3-trap-729
description: "|net|=3 confluence bucket hits 30.8% (n=26) — WORST bucket in signal_confluence_net. |net|=4 hits 75% (n=20). Signal is NOT monotonic. Fix: |net|=3 should DOWNGRADE tier one grade, not lift it."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-29T20:28:16.483Z
---

**Set 2026-07-29 after full-lifetime confluence audit.**

## The finding

Confluence net → ML hit rate (last 14 days, n=210):

| |net| | Record | Rate |
|---|---|---|
| 1 | 22-21 | 51.2% |
| 2 | 21-15 | **58.3%** ✓ |
| **3** | **8-18** | **30.8%** 🚨 |
| **4** | **15-5** | **75.0%** ⭐ |
| 5 | 6-8 | 42.9% |
| 6 | 4-3 | 57.1% |

**Signal is NOT monotonic.** Jumping from |net|=2 to |net|=3 the win rate CRATERS from 58% to 31%. Then |net|=4 goes back to 75%.

## Why this happens (hypothesis)

|net|=3 is the "loud but not overwhelming" bucket. Likely mechanics:
- 3-4 cohorts agree on one side, 2 cohorts dissent
- The mix is often "loud winners + h2h_recent_home + bp_taxed" — the two known trap cohorts (see [[project_cohort_inversion_729]]) pull the net toward |3| by adding a bad vote to a real signal
- When the confluence stacks HIGHER (|net|=4+), the trap cohorts get outvoted; when it's lower (|net|=2), the trap cohorts drag the net down where they belong

Basically: |net|=3 is the sweet spot for trap cohorts to poison an otherwise-decent signal.

## Fix spec

1. **In compute_primary_play (mlb_pipeline/game_context.py):** when signal_confluence_net magnitude == 3, DOWNGRADE the tier one grade
   - PRIME → STRONG
   - STRONG → LEAN
   - LEAN → LIGHT (or skip)

2. **Log the downgrade** in primary_play sub-note: "|net|=3 trap zone — auto-downgraded"

3. **Track over next 15 fires** to validate — if downgraded picks in |net|=3 window hit at ≥55%, the downgrade rule is right; if the downgraded picks still hit <45%, we need to SKIP those games entirely.

## Interaction with cohort inversion

If we ship the cohort inversion from [[project_cohort_inversion_729]] first, the |net|=3 trap may naturally shrink (fewer trap cohorts pulling votes to that bucket). Suggested order:
1. Ship cohort inversion first (bigger blast radius, cleaner metric)
2. Wait 2 weeks, re-audit
3. If |net|=3 still shows the trap, ship the downgrade rule

If we want to ship both simultaneously, tag both with the same shadow-breakdown column so we can attribute lift correctly.

## How to apply

- File: `mlb_pipeline/game_context.py` (compute_primary_play function)
- Placement: BEFORE tier assignment logic
- Guard: only apply when signal_confluence_net is fresh (not stale)

## Related
- [[project_cohort_inversion_729]] — sister fix, likely root cause of the trap
- [[project_side_resolver_wired_611]] — resolver where the tier decision fires
- [[project_ml_rl_and_new_tiers_727]] — new tiers where this downgrade fits
