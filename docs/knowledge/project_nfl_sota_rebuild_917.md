---
name: project-nfl-sota-rebuild-917
description: 6-phase state-of-the-art NFL model + prop logic rebuild. Every phase shadow-validates ≥4 weeks + beats live ≥3pp on n≥50 before promoting. Phase 0 infra shipped 2026-09-17
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-18T02:43:21.074Z
---

**Andy directive (2026-09-17):** "I want to dig deep to make nfl model and prop logic state of the art how do we get there? I want to do the whole thing... obviously not change anything this weekend because we would need appropriate backtest or shadow to assess"

**Cost constraint:** Not increasing operating costs until off the ground. No PFF, no Next Gen Stats, no paid data. nflverse (free), Odds API (existing), Sleeper/ESPN projections (existing) only.

**Promotion gate for every change:**
    picks_count >= 50 AND (shadow_hit_rate - live_hit_rate) >= 0.03
    sustained ≥ 4 weeks before promoting shadow → live

## Phase status

**✅ Phase 0 — Foundation (shipped 2026-09-17)**
- Silent-failure sweep: 10 commits (49f037db → 2bd60f0b) — see [[project_nfl_silent_failure_sweep_917]]
- Migration `20260917i_nfl_shadow_v2_infra.sql`: adds `primary_play_shadow_v2` + `playbook_snapshot_shadow_v2` + `shadow_v2_backtest_results` table
- Backtest harness: `mlb_pipeline/shadow_v2_backtest.py`
- Data completeness verified: targets/target_share/air_yards_share/wopr/YAC all 100% populated (3872 player-weeks 2025, 221 2026)

**⬜ Phase 1 — Prop usage signals (1-2 weeks, biggest cheap lift)**
- WOPR gate (elite ≥0.55 / high ≥0.45 / low ≤0.20)
- Air_yards_share gate (deep threats)
- Raw target volume (8+/game = OVER lean)
- YAC signals for rec_yds props
- Extend all to UNDER + rush_yds
- Shadow variant name: `usage_v2`
- Data cost: zero — all columns populated

**⬜ Phase 2 — Prop matchup signals (2-3 weeks, needs data source decision)**
- WR vs CB coverage grade
- RB vs LB tackling grade
- QB vs blitz + pressure
- Data path (per Andy no-cost constraint): nflverse PBP-derived approximations
  (not PFF grades). ~70% of value for $0.

**⬜ Phase 3 — Game model deepening (3-4 weeks)**
- PBP-derived team EPA (nflverse, free)
- SoS-adjusted stats (own implementation, like SP+/DVOA)
- Per-team weather elasticity coefficient
- Position-weighted injury impact
- Ref crew signals (nflref.com scrape)

**⬜ Phase 4 — Betting market integration (2 weeks)**
- Fix OC coverage gap first ([[project_nfl_oc_coverage_gap_917]])
- Line movement tracker
- RLM (reverse line movement) flag
- Prop line movement per book

**⬜ Phase 5 — ML architecture (2-4 weeks)**
- Meta-ensemble (learn model weights per cohort)
- Isotonic calibration per (sport, market, tier, situation)
- SHAP feature attribution

**⬜ Phase 6 — Live retraining (2 weeks)**
- V4 weekly retrain on rolling 2-year window
- Automated backtest gate on promotion
- Model version control

**Total buildout: 12-17 weeks for full SOTA.**

## How to add a shadow variant

1. Write a script that computes candidate primary_play (or playbook_snapshot) with new logic
2. UPSERT to `nfl_game_context.primary_play_shadow_v2` (or `nfl_pipeline_props.playbook_snapshot_shadow_v2`)
3. Tag the JSONB with `"variant": "your_name"` — required
4. Schedule to run every cron alongside live scorer (parallel, not overwriting)
5. Grade weekly: `python shadow_v2_backtest.py --sport NFL --variant your_name`
6. Read `shadow_v2_backtest_results` for trend; promote when gate passes 4 consecutive weeks

## Files
- `supabase/migrations/20260917i_nfl_shadow_v2_infra.sql` — shadow columns + backtest results table
- `mlb_pipeline/shadow_v2_backtest.py` — replay harness (sport, market, variant scoped)

Related: [[project_calibration_architecture_805]] (mechanical calibration + Jerry narrator split — matches the shadow discipline), [[project_signal_framework_821]] (signal-source discipline checklist all Phase 1+ signals must pass).
