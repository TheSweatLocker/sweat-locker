---
name: mc-hc-recalibration-729
description: "MC HIGH-CONF is 40% (4-6) over last ~10 fires per daily_grades. Yesterday's BOS MC-HC 94% loss wasn't a one-off. Fix spec: freeze MC-HC as auto-PRIME driver until 15-fire calibration audit confirms it's back."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-29T20:28:39.804Z
---

**Set 2026-07-29 after full-lifetime audit found MC-HC picks at 40% hit rate over last ~10 fires.**

## The finding

From daily_grades pick_type breakdown (n=169 total, last ~5 weeks):

| Pick type | Record | Rate |
|---|---|---|
| prop_prime | 35-21 | 62.5% |
| prop_strong | 35-22 | 61.4% |
| primary_play (all tiers) | 10-8 | 55.6% |
| nrfi_ensemble | 11-12 | 47.8% |
| **mc_high_conf** | **4-6** | **40.0%** 🚨 |

Yesterday (7/28) BOS ML at MC-HC **94.4%** was the highest single-game MC confidence of the day. It LOST 3-4 to OAK on a walk-off. That was the second confidence-4-loss in a row from the MC-HC surface.

## Why this matters

MC HIGH-CONF drives auto-PRIME tier assignment in compute_primary_play. When MC says 90%+ win prob, the pipeline promotes it to PRIME regardless of lens agreement. A 40% hit rate on picks tagged PRIME:
1. Erodes public trust (biggest badge = biggest disappointment)
2. Correlates with the |net|=3 trap and the h2h_recent_home fade cohort (see sibling memories)
3. Suggests the Monte Carlo simulator has an input bias — likely pulling from a stale distribution or the winning-side price adjustment

**Sample is small (n=10)** — could be variance. But given yesterday's specific 94.4% miss on a public card, the risk of another loud MC-HC miss burning us in the next 5 fires is high enough to warrant a temporary gate.

## Fix spec (in priority order)

1. **IMMEDIATE (before tonight's post):**
   - In compute_primary_play, when tier is being set FROM mc_high_conf alone (no supporting lens majority), downgrade PRIME → STRONG
   - Only auto-PRIME on MC-HC when 4+/6 lens ALSO agree with MC direction
   - Add sub note: "MC-HC downgraded — pending calibration audit ([[project_mc_hc_recalibration_729]])"

2. **This week:**
   - Full audit of MC simulator inputs — is it pulling stale prices? stale weather? outdated lineup?
   - Check if the `mc_probabilities.mc_expected_margin` values match the mc_home_win_prob (internal consistency)
   - Compare MC-HC hit rate before/after any recent MC simulator changes

3. **Two weeks:**
   - If MC-HC has fired 15+ times with the downgrade rule and hit ≥60%, restore auto-PRIME
   - If MC-HC still <50%, either (a) fix the simulator, (b) demote MC-HC to just a factor in confluence rather than a tier driver, or (c) kill it

## How to apply the immediate gate

File: `mlb_pipeline/game_context.py` compute_primary_play

Guard pattern:
```python
if mc_high_conf_flag:
    # Require lens majority support before auto-PRIME
    lens_agree = sum(1 for s in [panel_side, jerry_side, v3_side, v4_side, conf_side]
                     if s == mc_high_conf_side)
    if lens_agree < 4:
        # Downgrade — MC alone isn't enough given recent 40% calibration
        tier = 'STRONG'  # from PRIME
        sub_note += ' · MC-HC lens-support gate: only ' + str(lens_agree) + '/5 lens agree, holding STRONG'
```

## Track over next 15 fires

Log every MC-HC fire with:
- MC probability (0.90-0.99 bucket)
- Number of supporting lens (0-5)
- Whether tier got downgraded
- Result

After 15 fires, decide: fix, keep gate, or promote back.

## Related
- [[project_cohort_inversion_729]] — sister audit finding
- [[project_confluence_net3_trap_729]] — sister audit finding
- [[project_ml_rl_and_new_tiers_727]] — MC-HC tier logic
- [[feedback_confidence_in_first_pass]] — honest confidence rule
