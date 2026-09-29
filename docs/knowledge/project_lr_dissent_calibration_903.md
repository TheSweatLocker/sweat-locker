---
name: project-lr-dissent-calibration-903
description: "🎯 9/3 MLB audit: LR 7-1 sides, PRIME props 19-3. Dodgers game — LR shadow WRONG, consensus-dissent gate correctly protected. LR total 6-1, LR ml 1-0."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-04T12:13:30.058Z
---

## 9/3/2026 MLB — final graded audit

**9 games, all graded.**

### Sides (post-clean)

| Engine | W-L-P | Hit% |
|---|---|---|
| LR total | 6-1 | 85.7% |
| LR ml | 1-0 (Astros) | 100% |
| Ensemble (COVERAGE) | 1-0 (Dodgers) | 100% |
| **Total** | **8-1** | **88.9%** |

Only sides miss: **BOS/BAL Under 8.5** (11 total, blown out).

### The Dodgers case — gate was RIGHT

**Correction to earlier notes.** Dodgers WON 3-2 vs Cardinals. So:

- LR shadow said Cards PRIME (`p_home_win=0.1386` → LR gave LAD only 13.86% chance) → **LR WRONG**
- Ensemble said Dodgers (8 sources, panel +1.94, Dimers 44-9, 93% money HOME) → **ENSEMBLE RIGHT**
- Consensus-dissent gate blocked LR override → **GATE WORKED CORRECTLY**
- Final tier: COVERAGE (LOW CONVICTION chip) — honest read to user

**Why:** The gate was designed to prevent exactly this failure mode — LR alone bucking a strong ensemble+market signal. It worked as intended on 9/3.

**How to apply:** Do NOT loosen the gate on the strength of one day. The tracker will accumulate the calibration data over the next 30 days. Only touch the gate when v_lr_dissent_hitrate has n≥30 dissents graded.

### Props (post-scratch-sweep)

| Tier | W-L | Hit% |
|---|---|---|
| PRIME | 19-3 | 86.4% |
| STRONG | 4-1 | 80.0% |
| LEAN | 15-12 | 55.6% |

Sweep caught **9 phantom pitcher props** (1 Nick Martinez, 4 Ranger Suarez, 4 Shane Baz) that were mis-attached to wrong game_ids and would have graded as noise. All demoted to SCRATCHED.

Standout prop types on 9/3: **ha_under 5-0**, **ks_over 3-0**, **outs_under 5-2**.

### Sweep bug found + note

The sweeper's tier PATCH works, but a subsequent manual `generate_props.py` run UPSERTs the row and blows away `tier=SCRATCHED`, restoring the phantom. Cron chain (sweep after generate_props) prevents this in normal ops. **How to apply:** never run generate_props alone without also running sweep_pitcher_scratches after — or wire generate_props to skip UPSERT on rows already SCRATCHED (post-launch).

### Surface record status (as of 9/3 audit)

**Positive:** prop epoch +73.05u (119-50, 70.4%), sharp epoch +11.72u (57.4%), potd epoch +5.09u (73.3%).

**Negative:** dawg epoch -3.36u, ladder epoch -3.18u, ledger epoch -2.28u. Dawg + ladder need attention post-launch.

## Related

[[feedback_verify_ml_direction]] · [[project_dissent_audit_822]] · [[project_calibration_architecture_805]] · [[project_per_source_tracker_moat_818]]
