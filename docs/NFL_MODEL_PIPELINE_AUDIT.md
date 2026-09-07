# NFL Model + Pipeline Theory — Audit & Walkthrough

**Written 2026-09-07 evening** as launch-week technical reference (per user request during post-launch queue work). Companion to `memory/project_nfl_model_pipeline_discussion_907.md`.

## Purpose

Answer four questions comprehensively:

1. How do we currently project NFL games?
2. What's in the model stack?
3. Is the NFL prop pipeline similar to MLB?
4. Is LR (supervised logistic regression) live for NFL props? Is the filter-to-strong-signal process smooth?

---

## 1. NFL Game Projection Stack

Every NFL game gets scored by SIX distinct model layers, aggregated into `mlb_pipeline/nfl_game_context` row + `primary_play` verdict.

### Layer 1 — EPA-matchup model (v3-era)
- **Source**: per-team season EPA per play (offense) × opp EPA per play (defense allowed)
- **Output**: `projected_total`, `projected_spread` (H+/A- convention: positive = home favored)
- **Fields on ctx**: `v3_spread`, `v3_total` (parallel names)
- **Strengths**: cheap, always-on, reasonable for base rates
- **Weaknesses**: doesn't handle QB changes, weather, injury outs

### Layer 2 — Panel model (fantasy-aggregated)
- **Source**: aggregates individual player projections (Sleeper + ESPN fantasy feeds) into per-team point projections
- **Output**: `panel_pred_home_pts`, `panel_pred_away_pts`, `panel_pred_total`, `panel_confidence`
- **Ships weekly** (~Wednesday) as fantasy sources finalize
- **Strengths**: reflects starter injury outs (fantasy analysts do the work for us); catches usage shifts
- **Weaknesses**: sensitive to fantasy consensus errors; total often systematically low vs actual

### Layer 3 — v4 model (offensive-side EPA model)
- **Source**: current-season per-team EPA weighted by opp-adjusted metrics
- **Output**: `v4_spread`, `v4_total`, `v4_confidence`
- **State**: SHIPPED but often NULL in early weeks (needs multi-week sample)

### Layer 4 — Monte Carlo probabilities
- **Source**: simulates game outcome N times using team score distributions
- **Output**: `mc_probabilities` JSONB — `mc_home_win_prob`, `mc_p_over`, `mc_expected_total`, `mc_expected_margin`, `mc_mean_total`
- **State**: SHIPPED for MLB (dense), thinner on NFL (needs distribution priors that require sample)

### Layer 5 — LR overlay (supervised logistic regression)
- **Trained**: on thousands of resolved MLB/NFL games, retrained Mondays
- **Output on ctx.primary_play**:
  - `_lr_p_home_win` (0.0-1.0)
  - `_lr_ml_shadow` (LR's cross-market opinion when pipeline picked a different market)
  - `_lr_juice_cap_reason` (e.g. `NFL_ml_odds_worse_than_-300` — LR endorses but juice too rich)
  - `_lr_model_version` (timestamp)
- **NFL LR live** — confirmed 9/7 via primary_play scan: 10 of 16 NFL games on 9/10-9/15 slate carry LR predictions. Perfect alignment with pipeline (no dissents).

### Layer 6 — Ensemble scorer
- **Consumes** all above layers + cohort tags + line movement flags
- **Emits**: `primary_play` JSONB with:
  - `label` — human "LA ML" / "Under 42.5"
  - `tier` — PRIME / STRONG / LEAN / COVERAGE / SKIP
  - `side` — HOME/AWAY/OVER/UNDER
  - `type` — ml / spread / rl / total / prop
  - `score` — 0-100 conviction
  - `sub` — prose signal citation string
  - `_losing_market_notes` — signals that fired on markets we did NOT pick (transparency)

### Sweat Score
Composite 0-100 game-level signal density (not per-market conviction). A game can be Sweat 88 (many signals firing) but have a COVERAGE-tier pick (all signals demoted by juice caps).

---

## 2. NFL vs MLB — Same Shape, Different Maturity

### Structural parity
Both sports flow through the same architecture:
- `<sport>_pipeline_props` — raw prop rows with tier + signals
- `prop_playbook_decisions` — playbook layer (BACK/FADE per direction)
- `prop_jerry_reads` — synthesized reads (LLM or template)
- `<sport>_game_context` — game-level lens outputs
- `jerry_reads` — game-level narrated reads
- `sharp_card_YYYY-MM-DD` in `jerry_cache` — curated ~8-item card
- `v_<sport>_props_publishable` — server-side filter view for app

### Maturity gaps (per 2026-09-07 audit)

| Layer | MLB | NFL | Notes |
|---|---|---|---|
| Player game logs | 100% populated | Fixed 9/7 (nflverse col rename) | Was NULL for INT/team/sacks on 2025 rows |
| Tier distribution | 17% STRONG (calibrated) | 80% STRONG (miscalibrated) | Self-corrects by Wk 4-6 |
| Prop `render_sections` coverage | 98% chart, 100% coverage | 100% sections, 62% chart | Rare markets (INT, anytime_td) still no chart data |
| Prop synth truncation | 96 rows fit 300-limit | 476 rows overflowed → bumped to 2000 (9/7 fix) | |
| Publishable view | `v_mlb_props_publishable` | `v_nfl_props_publishable` (9/7 new) | NFL adds Jerry BACK@conv60 gate for tier compensation |
| Jerry `pre_parsed_facts` | Shipped 9/7 | Shipped 9/7 (V3) | Both have anti-hallucination rules |
| Realigner-repair paths | 3 audit paths (F/H/etc.) | N/A | All 3 fixed to route audit → audit_notes column 9/7 |
| Playbook coverage | Full (30+ signal_sources) | Partial (base + Madden ratings) | NCAAF port shipped 8/9 |

### The prop pipeline is ~90% MLB-parity today

Post-9/7 fixes, NFL props flow through:
1. `nfl_generate_props.py` → per-prop scoring, LR overlay, l10 chart data
2. `prop_playbook_decisions` → playbook BACK/FADE (Madden + situational)
3. `generate_prop_jerry_synthesis.py --sport NFL` → template + LLM synthesis, now with render_sections attach on both paths
4. `v_nfl_props_publishable` view → app-side filter (non-SKIP + no coverage kill + Jerry BACK@conv60)
5. `_is_prop_publishable` + `_effective_prop_tier` gates for downstream surfaces

Every step has a MLB analog. Where they differ, it's calibration density — not structural.

---

## 3. Signal filtering: is it smooth?

Yes, but not automatic — it's a **layered gate system**:

### Prop filter waterfall (mirrors MLB)
1. **`_coverage_kill_gate`** — SQL view drops rows flagged as COVERAGE-tier stubs by `apply_refit_verdict_override`
2. **Tier gate** — surface = non-SKIP OR SKIP-with-Jerry-BACK
3. **Jerry verdict gate (NFL-only)** — `call_verdict='BACK' AND conviction >= 60` compensates for early-season tier miscalibration
4. **Refit override** — `apply_prop_refit.py` recalibrates conviction post-tier-assignment via LR + cohort ROI lookup
5. **Playbook decision** — separate BACK/FADE opinion via `apply_playbook_decisions.py` (currently shadow-mode for NFL)
6. **Signal gate over tier** — every prop composer MUST use `_is_prop_publishable` + `_effective_prop_tier` (per `feedback_signal_gate_over_tier_906`)

**Smooth?** Yes, once tiers calibrate. In Weeks 1-3 the compensating layers (Jerry BACK gate + refit + playbook) do heavy lifting. By Wk 4-6 the base tier calibration matures and the compensating layers become optional.

### Game filter (per `mlb_game_context.primary_play`)
Same shape as MLB — ensemble scorer picks the top market per game, tier maps to sizing (PRIME 2u / STRONG 1.5u / LEAN 1u / COVERAGE not surfaced). LR overlay can override tier when it strongly dissents. Post-9/7 the anti-hallucination `pre_parsed_facts` layer prevents Jerry prose from misrepresenting the underlying pick.

---

## 4. Known open items (post-launch backlog)

- **NFL tier recalibration** — 80% STRONG rate will drop toward MLB's 17% as Wk 4-6 sample thickens. Monitor via `apply_prop_refit.py` output logs.
- **NCAAF pre_parsed_facts + rules** shipped 9/7 (this session, batch 3)
- **Best-available odds compare view** — post-launch v1.1
- **Ladder re-check-at-read** — shipped 9/7 (Batch 1)
- **Book selector** — shipped 9/7 (Batch 2)
- **v_nfl_props_publishable threshold tunable** — the `pj.conviction >= 60` gate is an ALTER-VIEW away from being loosened as calibration matures

---

## Files touched during this audit (for reference)

Backend:
- `mlb_pipeline/nfl_player_stats.py` (column rename fix)
- `mlb_pipeline/nfl_generate_props.py` (300 → 2000 limit, INT column now flows)
- `mlb_pipeline/generate_nfl_game_reads.py` (V1→V2→V3 anti-hallucination)
- `mlb_pipeline/generate_prop_jerry_synthesis.py` (render_sections attach LLM path)
- `mlb_pipeline/generate_mlb_game_reads.py` (pre_parsed_facts port)
- `mlb_pipeline/generate_ncaaf_game_reads.py` (pre_parsed_facts port)
- `mlb_pipeline/jerry_pre_publish_audit.py` (3 audit-repair paths → audit_notes)

Migrations:
- `20260907c_fix_placeholder_scrub.sql` (roster-talent {token} scrub)
- `20260907d_jerry_anti_hallucination_rules.sql` (NFL rules)
- `20260907e_nfl_props_publishable_view.sql` (Jerry BACK@60 gate)
- `20260907f_mlb_jerry_anti_hallucination.sql` (MLB rules)
- `20260907g_ncaaf_jerry_anti_hallucination.sql` (NCAAF rules)

Frontend:
- Direction fallback, sub-chips, LOW-conviction demote, LineMovement no-open, Model Consensus label, ladder stale-check, Home hot-streak + record chips, book selector, sportsbook disclosure.
