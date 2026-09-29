---
name: project-ledger-leg-selection-910
description: "30d Ledger analysis — chalk_parlay is the only profitable kind, teasers bleeding. Leg-selection redesign recommended before v1.0.1"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-11T19:02:08.333Z
---

**30D Ledger performance (2026-08-11 → 2026-09-10, 125 graded rows):**

| kind                    | W-L    | pct | avg_odds | net    |
|-------------------------|--------|-----|----------|--------|
| chalk_parlay            | 29-31  | 48% | +131     | **+7.35u** ✅ |
| hits_parlay             | 1-3    | 25% | +188     | -0.60u |
| teased_spreads_combo    | 4-5    | 44% | -165     | -3.09u |
| teased_totals_combo     | 5-8    | 38% | +61      | -2.95u |
| teaser                  | 14-25  | 36% | +155     | -2.57u |
| **TOTAL**               | 53-72  | 42% | —        | **-1.86u** |

**14D "down bad" window (94 graded):** -7.49u — this is the pain user feels.
- teaser -5.77u (12-25, 32%) ← biggest bleeder
- teased_spreads_combo -2.84u (1-3)
- chalk_parlay +1.72u (22-27, 45%) — still positive

## Diagnosis
- **chalk_parlay is the ONE profitable kind** — hits 45-48% at +131, well above breakeven (~43%). Should be the workhorse.
- **Teasers are the bleeder** at avg +155 — teasing lines that aren't confident enough. Payout doesn't justify the miss rate.
- **teased_spreads_combo** juice too heavy (-165 avg): when lost, drop 1u; when won, +0.6u. Bad payout math.
- Everything is 2-leg (avg_legs=2.0 uniform) — no 3-leg experimentation, missing chalk EV.

## Recommendations (before v1.0.1)

**Priority 1 — reduce teaser volume, promote chalk_parlay.** Cap teaser to ~1 per week (only when 2+ picks meet strict criteria: home fav ≥ -6, teasing to ≤ -1.5). Ship chalk_parlay every day.

**Priority 2 — try 3-leg chalk parlays** for high-density days. Current code caps at 2 legs. A 3-leg chalk at +250 with three 68%-conv picks has better long-run EV than 2-leg at +125 when picks are legitimately independent.

**Priority 3 — tighten teased_spreads_combo pricing.** Require target combined odds > +100 (currently -165 avg is upside-down math). Skip when it can't be built.

**Priority 4 — kill hits_parlay outright.** 1-3 sample in 30d, negative, only exists for MLB. Consumed a Ledger slot better used for chalk_parlay.

**Priority 5 — teased_totals_combo** could survive if we filter to only totals where the tease crosses a key number (7, 8, 9 for MLB · 41, 44, 47, 51 for NFL). Currently teasing arbitrary lines.

## Status — SHIPPED 2026-09-11 (commit 58c5d315)

User direction: "the ledger should be prime plays that are teased."
Applied to `generate_ledger.py`:
- All teased builders (totals, spreads, teaser) now require **tier == PRIME** on every leg (was PRIME/STRONG/LEAN). Teasing low-conviction picks was the source of the -2.57 to -3.10u bleeds.
- `chalk_prop_parlay` **KILLED** — worst 30d performer (-3.98u) and props can't be teased into safer zones, so they don't fit the "prime + teased" identity.
- `teased_totals_combo` **RE-ENABLED** — was paused 8/25 for bleeding on weak legs; PRIME gate is the real fix.
- `chalk_parlay` untouched — it's the +7.35u winner and its identity is chalk-ML combo (not teased), keeps shipping.
- Silently no-ops any kind that can't find 2 PRIME legs today. Better to ship 1 combo than a losing one.

Verified live today: 2 clean combos vs prior 8 (chalk_parlay +120 + teased_totals_combo +109, both PRIME).

## How to apply
Ledger is a v1.0.1 candidate for a re-tuning pass. Do NOT touch this pre-launch — the app is live tonight. Fix code in `generate_ledger.py::build_*` functions in the v1.0.1 cycle. Backtest change vs. current baseline over the same 30d window before shipping.

## Related
- [[project_ledger_performance_910]]
- [[project_v1_0_1_client_priorities]]
- [[project_ledger_teasers_817]]
