# Engine state — every sport, every model, measured

**As of 2026-10-26.** Andy: *"I just feel like we have a lot of fires per
sport on performance of engine and subsidiary models."* That is correct.
This page exists so the fires are countable and ordered instead of scattered
across fifty findings.

Every number here is measured, dated and sourced. **ROI is computed from the
price we actually published**, per row — not a flat −110, which inflated
three of my own moneyline claims earlier today
(`feedback_flat_110_inflates_every_ml_figure`).

---

## The one-line summary

**One surface clears: MLB moneyline.** Everything else is at or below the
price. The engine is not uniformly broken — our moneyline reads are
decent-to-good, and the failures concentrate in **football spread side
selection** and **props**.

---

## Sides and totals

| sport · market | measured | ROI | n | state |
|---|---|---|---|---|
| **MLB ML** | 63.9% vs BE 57.3% | **+11.02%** | 319 | ✅ **clears.** Live subset +22.83%, survives the 09-19 price floor, 10 of 12 weeks over BE. **Season over** |
| NCAAF ML | 59.5% vs BE 58.4% | −0.56% | 153 | ⚠️ breakeven. Live subset −18.38% |
| NHL ML | 58.2% vs BE 56.3% | +0.37% | 153 | ⚠️ breakeven, **and it is a home constant** — engine 110-78, "always take home" 110-78, identical, because we pick home 88% of the time |
| NCAAF spread | 49.4% | −5.65% | 346 | 🔥 loses. "Take home" on the same games was 58.4% |
| NFL spread | 45.8% | −12.64% | 59 | 🔥🔥 **below random side-picking.** "Take away" on the same games was 72.9% |
| NBA | — | — | 32 ML / 14 spread | ⬜ too thin to measure |

**The spread defect is side selection, not game selection.** On NCAAF, home
covered 58.4% *on the games we chose* while we went 49.4% — the selection
finds live games and the side goes the wrong way. Disagreement split: both
right 16.5%, only engine right 32.3%, only the away rule right 29.6%. The
engine is informative; it is pointed backwards.

### What has been tried on football spreads, and failed

| fix | result |
|---|---|
| the engine as-is | NCAAF 49.4%, NFL 45.8% |
| the flip (live on NCAAF) | 51.0–51.5% walk-forward — better than the engine, still under BE |
| fixed side rule (take home/away) | a **season regime**, and the regime mean-reverts |
| season-to-date regime feature | NFL 49.3% / NCAAF 50.2% — dead |
| 25 rolling-stat features, ridge, 24 seasons | 50.5%, **OOS R² = −0.0115** — worse than believing the market |
| 624 conditional cells, 28 seasons | **0 clear Bonferroni** |

That is six distinct attempts on one market. The conclusion is not "no edge
exists" — it is that **no linear function of what we store beats the NFL
closing spread**, measured on 4,444 out-of-sample games.

---

## Props

| sport | graded | ROI | tiering | state |
|---|---|---|---|---|
| MLB | 19,507 in band | **−6.4%** | PRIME/STRONG/LEAN exist | 🔥 the apparent edge **was the L5 leak**; repaired 10-26 (13,265 rows) |
| NFL | 3,968 in band | −3.1% | tiers exist, **inverted** | 🔥 PRIME is the *worst* tier (−10.94%); LIGHT is +3.37% |
| NBA | **8,760** (graded 10-26) | **−10.50%** | was all COVERAGE → now gated | 🔥 severe **over-bias**: overs −15.87%, unders −3.66% |
| NHL | 12,034 in band | −6.80% | **none — all COVERAGE** | 🔥 no discriminator exists at all; one numeric signal key |

**Published state:** NBA and NHL prop views return **0 rows on every date**.
Prop receipts exist only for MLB and NFL. So the NBA/NHL prop fires are not
currently user-facing — they are blocking those sports from ever being
published.

### The one discriminator that exists

`prop_line / bounded_L5_average`, measured on 8,638 graded NBA props, every
window bounded strictly before the game (0 bound violations):

```
OVER    Q1 61.0%  Q2 49.7%  Q3 44.0%  Q4 35.0%  Q5 24.0%
        ROI  -2.00%  -10.42%  -13.24%  -18.18%  -34.82%
UNDER   Q1 44.2%  Q2 50.9%  Q3 55.8%  Q4 55.4%  Q5 57.6%
        ROI -12.39%   -5.66%   +0.46%   -0.08%   -0.89%
```

Monotone both directions, 37-point spread on overs. **The market prices
nearly all of it** — best cell +0.46% — so it is a ranking and banning
signal, not a licence to publish. Shipped as `nba_prop_gate.py`: OVER above
1.36 → SKIP, UNDER at/above 1.07 → LEAN, nothing gets PRIME or STRONG
because nothing measured profitable.

**The same ratio on MLB was the leak**, not a signal — rebuilt bounded, it
goes from +14.92% to −3.72%. So do not copy the MLB prop recipe anywhere.

---

## Conviction and tiering — the cross-cutting fire

| sport | conviction spread | does it sort? |
|---|---|---|
| NCAAF | median 63, 56% ≥60 | non-monotonic; 65-74 is the **worst** bucket |
| NHL | median 59, 49% ≥60 | n/a on props (all COVERAGE) |
| **NBA** | **median 52, max 65, only 14% ≥60** | **cannot** — it barely varies |
| NFL props | flat | corr(conviction, price) = −0.002, every bucket −2.3 to +1.0 |
| MLB props | looked monotonic 48.5→67.0% | **that was the leak** |

**NBA reads are not "decent".** That claim rests on **73 graded receipts
total**, 39 of them in the 50s — 61.3% on n=31 and 69.2% on n=13. Neither
means anything, and conviction spanning 50–65 cannot rank.

---

## Data integrity fires (each blocks something already proven)

| defect | impact |
|---|---|
| `open_spread` == `close_spread` on **1,967/1,967** | we hold **no line-movement data** — the only feature that ever survived a sweep |
| `nfl_game_results.wind` NULL since **2026-10-11** | the validated wind lens is dark; writer is in no workflow |
| NCAAF weather 69% missing | the lens cannot ship on the 15,655-game sport |
| `nba_pipeline_props.player_id` NULL on every row | prop joins must use names — latent collision hazard |
| `team_stats_rolling` has **no history** | cannot be backtested at all; only current state |
| wrong-side receipts | 11 of 326 disagree with the box score (B171/B181) |

---

## What to fix first, and why

1. **B205 — schedule `backfill_nfl_result_weather.py`.** Restores the only
   validated lens and is the only way to answer whether it works on
   forecasts. One workflow line.
2. **B200 — populate `open_spread` from `line_history`.** Unblocks the one
   feature that ever survived a feature hunt.
3. **B207/B208 — NBA/NHL props.** Gate shipped for NBA; NHL needs its
   lookback built (bounded from line one).
4. **B171 — move the flip before the receipt writer.** Stops wrong-side
   receipts recurring every Saturday.
5. **Stop ranking by conviction** where it is measured flat or inverted
   (NFL props, NBA, NCAAF 65-74).

**What NOT to spend more time on:** another model on football spread sides.
Six approaches have now failed on 4,444 out-of-sample games with a negative
R². That market is efficient against everything we store.
