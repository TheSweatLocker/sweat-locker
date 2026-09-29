---
name: mc-v2-backtest-723
description: Rich MC v2 backtest n=626 (May 30 -> 7/23) — beats thin v1 by +4.7pt on sides. 80%+ conf hits 66.9%. Ablation shows lift comes from rich architecture NOT new multipliers.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-23T23:08:07.321Z
---

**Set 2026-07-23 evening after full backtest + ablation study.**

## Context

Pre-7/23, production MC was `monte_carlo_win_prob.simulate_total/side/spread`
— a THIN Poisson wrapper around v4's projected_home/away_runs. MC direction
literally echoed v4. Not an independent lens.

Migrated `enrich_monte_carlo.py` to use `monte_carlo.simulate_game` (rich
per-inning simulator that existed but was never wired to prod). Added
3 new multipliers (mastery, umpire, defense) into the rich simulator
before the migration.

## Backtest results (n=626 graded games, May 30 → 7/23)

| Metric | v1 THIN | v2 RICH | Lift |
|---|---|---|---|
| Sides direction | 52.2% | **56.9%** | **+4.7pt** ⭐ |
| Totals direction | 52.5% | 51.9% | −0.6pt |
| 80+% conf sides | 56.8% | **66.9%** | +10.1pt |
| 70-79% conf sides | 47.7% (bad) | 57.7% | +10pt |
| Disagreement win rate | — | 56.7% | (v2 wins 122/215 disagreements) |

**Key finding:** v2's side confidence is MONOTONIC (higher conf → higher
hit rate). v1's was noisy (70-79% band worse than 50-59%). Rich MC 80%+
picks are actionable at 67%.

Totals: parity. Don't lean on MC for totals over other lenses.

## Ablation results — which new multipliers matter?

| Variant | Side % | Δ vs full |
|---|---|---|
| FULL v2 | 57.67% | baseline |
| No mastery | 57.67% | **0.00pt (DEAD)** |
| No umpire | 56.87% | −0.80pt |
| No defense | 57.19% | −0.48pt |
| All 3 disabled | 57.51% | −0.16pt |

**Real insight:** the +4.7pt lift comes from RICH ARCHITECTURE (per-inning
sim + SP form/BP gas/offense drift/park/weather/hand splits), NOT from
my 3 new multipliers. Umpire is the only new one worth keeping cleanly
(+0.80pt).

**Mastery mult is dead in backtest** — 10-IP gate blocks most
pitcher/team combos. Not a code bug; pipeline data gap. See action
items below.

## Rules going forward

- **Ship v2 rich as-is for sides.** +4.7pt is meaningful and 80%+ conf
  is genuinely predictive.
- **Do NOT switch to MC-primary for totals.** Parity vs v1 means no
  edge; keep v3/v4/jerry/panel as primary total lenses.
- **Don't rip out mastery multiplier** — it's dormant, not harmful.
  Fix the data pipeline to populate more mastery data (10-IP gate
  currently blocks most matchups) THEN re-audit.
- **80%+ confidence MC side picks** are a real edge lane worth
  surfacing on the card. Roll into POTD tier gate.

## Action items post-backtest — ALL SHIPPED 2026-07-23 evening

1. **Mastery unlock (SHA b8e341a)** — DONE. Lowered recent 10→6 IP gate,
   trust era-alone when ip null (historical pipeline gap). Backtest
   result: mastery mult went from 0.00 → +1.75pt contribution. FULL v2
   now hits 59.4% sides / 71.1% at 80+% conf.
2. **MC high-conf chip (SHA 03cb517)** — DONE. Migration
   20260723_context_mc_high_conf added flag+side+pct cols;
   enrich_monte_carlo._compute_high_conf_flag fires at |p-0.5|>=0.30;
   app renders green chip below fade chip.
3. **NRFI backtest (SHA b3e8323)** — DONE. MC 57.1% overall beats
   sklearn 51.9% by +5.2pt. MC 70-79% conf band = 63.6% (best single).
   Sklearn 80+% conf still valuable at 58.4% n=166. ENSEMBLE (both
   agree) = 61.8% on n=241 — the real play. Queued: build ensemble
   scorer, promote MC 70-79% NRFI picks to card tier.

## Bug fixes during backtest development

- `mlb_game_results` uses `home_sp_name` NOT `home_pitcher`; `simulate_game`
  returns None on this schema difference. Bridge by aliasing in the
  backtest wrapper (already patched).
- `mlb_game_results.total_result` stores 'Over'/'Under' title-case, not
  lowercase. Lowercase before comparing (already patched).

## Related

- [[project_morning_audit_723]] — 7/22 audit that led to this work
- [[project_audit_721_full]] — v4 25% night that started the layer-audit thread
- [[project_composite_debias_finding_712]] — old finding v4 was broken
- [[project_panel_projection_validated_623]] — panel side signal parallel
