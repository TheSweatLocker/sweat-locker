---
name: refit-v2-expansion-810
description: "2026-08-10 design: expand refit to every prop type + auto-flip Jerry BACK/FADE on 20+ delta + weekly retrain + no-refit cap-to-LEAN."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-10T22:04:00.307Z
---

**Decision date**: 2026-08-10

**Problem observed today**: raw conviction 74 → refit 22 on Wilyer Abreu hits_over 0.5 (trap). Raw 75 → refit 100 on Logan Henderson K OVER (lock). Refit knows things raw doesn't but currently isn't wired into Prop Jerry BACK/FADE decision at all.

## Design (all decisions user-approved 8/10)

**Refit authority**: FORCE FLIP on 20+ point delta.
- Raw 74, refit 22 → Force FADE (raw said BACK, refit says trap)
- Raw 75, refit 100 → Force BACK (raw was already BACK but refit confirms and boosts)
- Raw 69, refit 38 → Force PASS (>=30 delta but signals conflict too much)
- Raw 70, refit 68 → HOLD raw (small delta)

**No-refit coverage**: cap conviction at LEAN 55 + transparency footer.
- Applies to props whose type isn't in refit registry (today: er_over/under, ha_over/under)
- Users see clearly that these picks lack calibration
- Prevents claiming PRIME confidence on uncalibrated picks

**Retrain cadence**: weekly (Sunday morning cron before Monday slate).
- `_fit_prop_refit_weights_v2.py --retrain` on Sunday 6am ET
- Uses trailing 60 days of graded data
- Writes new weights file with fresh version stamp
- `jerry_pre_publish_audit.py` fails if `refit_version >= 14 days old`

## Coverage expansion (refit v2)

Extend registry to cover EVERY prop type we ship:

Priority 1 (today's uncovered picks):
- er_over, er_under (Painter, Kremer, Taillon on today's card)
- ha_over, ha_under (Hughes, Henderson on today's card)

Priority 2 (batter props):
- hits_over_1_5 (currently only hits_over_0_5 which is signal-only)
- hr_over
- rbi_over

Priority 3 (any others discovered):
- stolen bases, total bases, doubles, etc — as encountered

## Sanity gates (extend jerry_pre_publish_audit.py)

- refit_conviction populated on >=95% of BACK/FADE picks (today: 0/16 = fail)
- refit_version <=14 days old
- No BACK verdict where refit <=40 (surviving trap)
- No FADE verdict where refit >=65 (fighting refit)

## Cron order (post-build)

1. compute bucket ROIs
2. **apply_prop_refit v2** (with expanded coverage)
3. generate_prop_jerry_synthesis
4. collapse_prop_jerry_contradictions
5. collapse_pitcher_thesis_contradictions
6. **apply_refit_verdict_override** (NEW — the force-flip pass)
7. **jerry_pre_publish_audit** (with new refit gates)
8. generate_sweat_card

## Weekly cadence add

Sunday 6am ET:
- `python _fit_prop_refit_weights_v2.py --retrain` (before regular cron)
- Logs new weights hash + coverage report to jerry_cache

Related: [[project_prop_edge_calibration_july]] (original refit design), [[project_launch_queue_806]] (post-launch queue).
