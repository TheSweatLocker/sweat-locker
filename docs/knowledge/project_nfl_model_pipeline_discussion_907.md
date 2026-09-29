---
name: project-nfl-model-pipeline-discussion-907
description: "🎯 9/7 queued: user wants deep discussion on NFL model theory + prop pipeline audit — how we project, what models we have, MLB parity, LR presence, filter-to-strong-signal process"
metadata:
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T20:02:14.469Z
---

**Queued 9/7 during launch-week data-gap audit.** User is questioning the NFL side end-to-end. Wants a comprehensive walkthrough / audit not just a bug fix.

## What user asked for

- **NFL model theory:** how do we currently project NFL games? What's the full model stack?
- **NFL prop pipeline:** is it similar to MLB? What's the same, what differs?
- **LR presence:** is Logistic Regression running for NFL props like it does for MLB props?
- **Signal filtering:** is the process of recognizing "strong signals" the same in NFL as MLB? Smooth? Or ad-hoc?

## What we know today (based on 9/7 audit findings)

- **Game model stack** (per generate_nfl_game_reads.py + nfl_game_context):
  - EPA-matchup model (offensive-side EPA × opp defense EPA) → `projected_total`, `projected_spread`
  - Panel model (fantasy-aggregated player projections) → `panel_pred_total`, `panel_pred_home_pts`, `panel_pred_away_pts`
  - v3/v4 model outputs → `v3_spread/total`, `v4_spread/total`
  - Monte Carlo probs → `mc_probabilities`
  - LR overlay → `_lr_p_home_win` in primary_play (confirmed live on 9/7)
  - Ensemble scorer → `primary_play` (label + tier + score + side)
- **Prop pipeline** (nfl_generate_props.py):
  - Mirrors MLB shape (nfl_pipeline_props.tier + signals + refit_conviction)
  - Uses per-week nfl_player_stats as source data for L10 charts
  - Signal-emit pattern (l5_confirm/l10_hot/weather/game_script) matches MLB
  - **BUT** tier calibration is miscalibrated in early season: 80% labeled STRONG (vs MLB's 17%). Compensated in v_nfl_props_publishable with an added Jerry BACK@conv>=60 gate.
  - LR overlay on props: confirmed live (signals._lr_p_hit is present, mirrors MLB)
- **Filter pipeline**: currently the same shape as MLB (playbook_decisions + refit + LR + tier gate), just tier miscalibration inflates STRONG in early weeks.

## Known NFL-vs-MLB deltas to discuss

1. **Tier calibration is looser** in first weeks (fewer historical samples). Self-corrects by Wk 4-6.
2. **Prop volume is much higher** — MLB slate 96 raw / NFL slate 476 raw. Fixed 9/7 via v_nfl_props_publishable filter to ~133.
3. **Game read prompt** now has anti-hallucination `pre_parsed_facts` + rules migration (fixed 9/7). MLB prompt does NOT have that layer yet — port pending.
4. **Player-stat backfill** had a nflverse-column-rename bug (9/7 fix). 2025 QB interceptions column was NULL across the board. Fixed in nfl_player_stats.py transform().
5. **Ladder logic** points at NFL via same code path but hasn't been battle-tested on NFL props (only picked from MLB props so far). Ladder stale-pick bug (Rodriguez 9/7) also on the todo list.

## What to cover in the discussion

- Full walk-through of the NFL game read stack (models → ensemble → primary_play → Jerry synthesis)
- Full walk-through of NFL prop pipeline (nfl_player_stats → nfl_generate_props → prop_jerry_synthesis → render_prop_template → v_nfl_props_publishable → app)
- Compare each stage to MLB — call out gaps, share-of-signal, calibration health
- Identify weakest links + where LR overlay could tighten
- Recommend NFL prop tier recalibration timeline (Wk 4-6?)
- Consider: should we lag NFL LR training until Wk 6 (like NCAAB) or roll retraining weekly?

## Related memories
- [[project_nfl_phase_2_shipped_809]] — 8/9 phase 2 ship
- [[project_nfl_ncaaf_week1_readiness_820]] — Week 1 signals wired
- [[project_signal_framework_821]] — cross-sport signal checklist standard
- [[project_lr_dissent_calibration_903]] — LR calibration tracking (MLB context)
- [[feedback_signal_gate_over_tier_906]] — every prop must eval _is_prop_publishable, not tier alone
- [[project_prop_playbook_port_817]] — port MLB playbook logic to other sports (in progress)
