# Deep dive queue — how each pick is made, selected, and written

Andy, 2026-10-27: "we need to curate more and engines like jerry read and
daily degen and anything in steam room need to be analyzed full for process
improvements (how a pick is made and selected and how the data is perceived
by engine writer). idk where to start."

Ordered by **measured cost to a paying subscriber**, not by how interesting
it is. The app has been live since ~10-06. Since then:

```
WHOLE PRODUCT        850-839   50.3%  n=1,689   -84.3u   ROI -4.99%
  last 7 days        278-297   48.3%  n=  575   -49.3u   ROI -8.58%

week 41  10-06..10-11   51.0%  n=529   ROI -4.10%
week 42  10-12..10-18   51.2%  n=560   ROI -2.99%
week 43  10-19..10-25   49.0%  n=575   ROI -7.50%   <- worst full week
```

Per surface, what they actually see:

```
prop_jerry    47.8%  n=950   -84.6u   ROI  -8.91%   <- 56% of all volume
game_read     55.2%  n=402    -3.1u   ROI  -0.76%
sharp_card    50.4%  n=125    -8.1u   ROI  -6.45%
sweat_card    58.9%  n= 90    +7.5u   ROI  +8.28%
fade          55.8%  n= 52    +3.9u   ROI  +7.47%
ledger        47.7%  n= 44    +6.9u   ROI +15.77%
potd          58.3%  n= 12    +1.2u   ROI +10.05%
daily_degen    8.3%  n= 12    -6.1u   ROI -50.75%
dawg           0.0%  n=  2    -2.0u   ROI -100.00%
```

**The headline: every surface we CURATE is profitable. Every surface we
MASS-PRODUCE loses.** −84.6u of prop losses against −84.3u total. The four
curated surfaces are carrying a firehose that cancels them out.

---

## D1 — `daily_degen`: the selection rule is inverted outside MLB

**29-137 all-time, 17.5%, on 168 parlays.** Worst surface in the product.

`jerry_anchor_daily_degen.py` has exactly one selection criterion:
`CONVICTION_THRESHOLD = 70`. Measured against game-read outcomes:

```
MLB    conv>=70  69.5% n=279   vs  conv<70  53.6%    works
NCAAF  conv>=70  50.3% n=187   vs  conv<70  51.2%    no difference
NFL    conv>=70  46.7% n= 15   vs  conv<70  56.2%    INVERTED
NHL    conv>=70  33.3% n=  3   vs  conv<70  55.8%    INVERTED
NBA    conviction caps at 65          -> can never qualify
```

It selects legs **less likely to win than the ones it rejects**, everywhere
except MLB. Its median leg conviction is 84, so it has been living entirely
off the top of the MLB scale — the single place the rule holds. **MLB ends
this week**, after which it draws from NCAAF/NFL/NHL where the criterion is
flat or backwards. It degrades on its own with no code change.

Also structural: a 3-4 leg parlay needs ~55%+ per leg to be viable. At 50%
a 4-leg parlay wins 6.25%.

**Dive:** stop publishing it today, then rebuild selection on something
measured. Do not re-ship on conviction.

## D2 — props: 56% of volume, −8.91%, and a model cannot save it

Fitted per sport, walk-forward, price removed from features, target =
beating the price. Best achievable slice:

```
NHL  baseline -11.32%   best top 30%  -3.85%   (orders cleanly: 72.3% vs 9.6%)
NFL  baseline  -2.65%   best top 30%  -0.01%   (control INVERTED)
NBA  baseline  -7.10%   best top 50%  -6.02%   (top 5% is -19.95%: anti-predictive)
```

**No sport's top slice goes positive.** Selection cuts the bleed and never
crosses zero. So this is a **composition decision, not a modelling one** —
the question is how much prop volume should exist, not how to rank it.

**Dive:** decide the prop volume policy first. Then, separately, whether
the surviving book is matchup-justified (D6).

## D3 — conviction is written by the LLM, not measured — and the scale is
## not comparable across sports

`generate_*_game_reads.py` hands the writer a `conviction_guidance` block
containing the tier and an instruction ("This pick is BELOW our publishing
bar"), and the writer emits `conviction`. It is a restatement of tier:

```
NBA tier -> conviction      scale ceiling per sport
  PRIME       -> 0  (!)       MLB  max 100,  35.0% >= 70
  PASS        -> 0            NCAAF max 100, 31.5% >= 70
  LIGHT_LEAN  -> 0            NHL  max  84,   2.9% >= 70
  COVERAGE    -> 50-62        NFL  max  79,  12.2% >= 70
  LEAN        -> always 59    NBA  max  65,   0.0% >= 70
  STRONG      -> 59-65
```

34 of 106 NBA reads are hard 0, **including both PRIME reads**. And it
correlates +0.452 with the engine's own `sweat_score` but **−0.010 with
winning**.

Consequence: every downstream gate using a fixed conviction threshold is
sport-blind — the degen's 70, the POTD gate, Sharp tier cuts. A 70 means
something in MLB and is unreachable in NBA.

One real finding to build on: conviction **does** work inside a price band.
`-180..-130` HI conviction 72.2% n=72 +19.20% vs LO 59.1% n=66 −1.53%, and
it holds split-half. In `-130..+100` it is INVERTED (48.3% n=230, −8.99%) —
and that band holds 58% of published reads. `conviction_band_gate.py` exists
in shadow.

**Dive:** stop the writer emitting conviction. Compute it from engine
quantities, per sport, and gate it by price band.

## D4 — Steam Room: composition and discipline

`sharp_card` 50.4% n=125 −6.45%. Spec is a 15-pick slate; actual over 14
days: 3,9,5,8,5,3,2,3,1,3,29,7,2,4 — mean ~5.3, one day at 1, one at 29.

On 10-26 it held 2 items, both MLB, both from the same game — and they were
the identical two picks as the Sweat Card, so the two surfaces collapsed
into one. Separate denominators is the entire point of having both.

It also published a POTD carrying
`reason = "⚠ Engine passed: MC sim has our side at 14% win prob"`
at conviction 76 for 2.0 units. It won. The gate still failed.

**Dive:** the gate chain from prop/read pool → tier resolve → item cap, and
why it produces 1 item some days and 29 on others.

## D5 — the Sweat Card publishes almost nothing, and it is the best surface

`sweat_card` is **+8.28%**, and `top_8` over 14 days ran
2,3,2,1,3,0,0,1,0,2,2,0,2,2 — mean **1.4**, four days at zero. `dawg` dark
14 of 14 days. `audit_roll_up`, `bucket_angle`, `lock`, `total_edges`,
`top_hits_*`, `top_ks_*` all empty on 10-26.

**This is the "curate more" target.** The surface that works is starved
while the surface that loses ships 950 picks.

**Dive:** why top_8 fills 1-2 of 8 slots. That is a supply problem in the
composer, not a discipline choice.

## D6 — how data reaches the writer

* `input_snapshot` is a **different schema per sport**, not one schema. Only
  3 numeric features exist on ≥80% of rows across sports, and two of those
  are the price. MLB carries ~50 rich features; NCAAF/NFL carry no moneyline.
* **159 of 590 NCAAF snapshots are a stub** holding only `{matchup, source}`.
  Those reads were written with no numeric inputs at all.
* The writer is handed the engine pick and told to "defend the side rather
  than derive his own" (`generate_ncaaf_game_reads.py`, the `engine_block`).
  So the prose is post-hoc rationalisation of a decision already made —
  which is defensible, but it means the read's reasoning is **not** evidence
  the pick is good, and it should never be read as such.

**Dive:** one shared snapshot contract with per-sport extensions, and a
hard refusal to write a read off a stub snapshot.

## D7 — `dawg` is 0-2 and dark

0 for 2 all-time since launch, slot empty 14 of 14 days. Either rebuild or
remove it from the UI; a permanently empty surface with a 0-2 record is
worse than no surface.

---

## Suggested order

1. **D1** — kill the degen today. It is 17.5%, it is in front of
   subscribers, and it gets worse this week with no action.
2. **D2** — set the prop volume policy. 56% of volume, −8.91%, unfixable by
   ranking.
3. **D5** — unstarve the Sweat Card. The one profitable surface fills 1.4 of
   8 slots.
4. **D3** — rebuild conviction per sport, gated by price band.
5. **D4** — Steam Room composition.
6. **D6** — the snapshot contract.
7. **D7** — dawg.

D1, D2 and D5 are the three that change the subscriber's week. D3/D4/D6 are
the ones that stop it recurring.
