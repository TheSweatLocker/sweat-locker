---
name: model-reweight-721
description: "60d reweight audit (n=753) found sub-50% cards are a SIDES problem, not totals. v4 spread + Panel margin were being under-used. Shipped conditional weighted composites."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  audit_date: 2026-07-21
  modified: 2026-07-21T16:18:55.509Z
---

**Ran 2026-07-21 after user asked "look at other models and reweight retroactively."**

## The one-line summary

**Sub-50% recent cards are a SIDES problem, not a totals problem.** v4 spread + Panel margin have been sitting on the shelf.

## Baselines (60d, n=753)

| Lens | Totals hit% | ATS hit% |
|---|---|---|
| v3 rules | 57.2% | 57.9% |
| v4 XGBoost | 51.2% (broken) | **58.4%** ⭐ |
| jerry-deb | 54.3% | 53.5% |
| Panel | (dropped) | 58.1% |
| Composite NEW flat (0.5 v3 / 0 v4 / 0.5 jerry-deb) | 57.9% | — |

Two facts:
1. **v4 on totals is dead (51.2% n=434).** Keep out of totals composite.
2. **v4 on ATS is the BEST individual sides lens (58.4% n=473).** We've been under-using it.

## TOTALS reweight — SHIPPED

Sample-robust default: **v3=0.5 / v4=0.2 / jerry-deb=0.3 → 58.59%** (+0.7pp)

**Bucket-conditional weights (72% in ace-matchup bucket!):**

| Bucket | Weights (v3, v4, jerry-deb) | Hit% | n |
|---|---|---|---|
| Ace matchup (both xERA ≤ 3.5) | 0.4 / 0.0 / 0.6 | **72.0%** ⭐ | 26 |
| Pitcher park (PRF ≤ 96) OR dome | 0.4 / 0.0 / 0.6 | 60.2% | 133 |
| Hitter park (PRF ≥ 105) | 0.5 / 0.1 / 0.4 | 60.5% | 43 |
| Default | 0.5 / 0.2 / 0.3 | 58.6% | 297 |

Shipped as `weighted_composite_total(v3, v4, jerry_deb, ctx)` in `tier_discipline_gate.py`. Wired into `play_of_day.py` via `ctx=pick.get('_ctx') or pick`.

## SIDES reweight — SHIPPED (bigger lift)

Panel-free default: **v3=0.5 / v4=0.3 / jerry=0.2 → 60.39%** on n=462 (+2.4pp over v3 alone)
Panel-included: **v3=0.1 / v4=0.5 / jerry=0.0 / panel=0.4 → 62.03%** on n=187 (+4.1pp)

**Jerry PULLS sides down** — all top-3 combos have jerry ≤ 0.2. This is the opposite of totals where jerry-deb is the strongest component.

Shipped as `weighted_composite_spread(v3, v4, jerry, panel)` in `tier_discipline_gate.py`. Wired into `play_of_day.py` `_comp_spread()` helper.

## Cohort net as weighted vote — REJECTED

Adding `signal_confluence_net` as a weighted vote to composite HURT accuracy:
- Totals: 59.04% → 57.89% (−1.15pp)
- Sides: 62.03% → 60.64% (−1.39pp)

Keep cohort_net as kill/flip resolver, don't add as weighted vote.

## Projected 60d impact

- Totals: modest (~+2-3 extra wins) from bucket-aware weights
- Sides: **~+15 extra wins** from proper v4/panel weighting
- **Recent sub-50% cards should reverse to profitable range once sides reweight is in production.**

## Ace-matchup formalization

72% hit rate on ace-matchup totals (n=26) is our loudest single bucket. Should surface as an ACE tag on cards. Track separately.

## Code shipped

- `tier_discipline_gate.py::weighted_composite_total()` — new
- `tier_discipline_gate.py::weighted_composite_spread()` — new
- `tier_discipline_gate.py::evaluate_total()` — accepts `ctx=` kwarg, uses weighted composite when ctx present
- `play_of_day.py::_comp_spread()` — swapped from flat avg to weighted_composite_spread
- `play_of_day.py::evaluate_total(...ctx=)` — passes pick or _ctx as ctx

## Related

- [[project_30d_lens_audit_718]] — precursor audit that first flagged v4-totals broken
- [[project_composite_debias_finding_712]] — jerry debias precedent
- [[project_audit_battery_721]] — parent audit battery
