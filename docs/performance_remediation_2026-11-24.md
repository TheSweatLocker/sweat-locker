# Why the card is losing, and what to do about it

**Measured 2026-11-24 from `public_receipts`** — the frozen published record, i.e.
what a subscriber actually saw, not `jerry_reads` (which a later cron can rewrite).
Every figure is at the **published price**, every percentage carries its `n`, and the
priced denominator is reported separately from the decided one.

---

## 1. The headline: conviction was tuned on MLB and never re-tuned for anything else

This is the whole problem in one table. Full history, decided and priced, `n=12,525`.

| conviction | MLB edge vs price | MLB ROI | non-MLB edge | non-MLB ROI |
|---|---|---|---|---|
| 0–49 | −2.0pp | −3.1% (n=1826) | −1.8pp | −3.4% (n=487) |
| 50–54 | −3.5pp | −6.6% (n=1095) | −3.1pp | −6.7% (n=1129) |
| 55–59 | −3.1pp | −4.8% (n=1313) | −2.2pp | −3.7% (n=631) |
| 60–64 | +1.0pp | **+1.9%** (n=437) | +0.5pp | +1.6% (n=482) |
| 65–69 | +3.6pp | **+6.1%** (n=763) | −1.7pp | −3.1% (n=1226) |
| **70–79** | **+6.3pp** | **+11.1%** (n=966, **+107.02u**) | **−4.7pp** | **−4.7%** (n=841) |
| 80–100 | +0.7pp | +1.0% (n=668) | **−3.9pp** | **−7.5%** (n=661) |

**In MLB, conviction is monotone and strongly predictive** — the edge rises −2.0 → −3.5
→ −3.1 → +1.0 → +3.6 → **+6.3pp**, and the 70–79 bucket alone made 107 units.

**Outside MLB it is inverted.** The higher the number we print, the worse the play.
Conviction 80–100 in non-MLB sports hits **44.3% against a 48.2% implied** — we are
most confident exactly where we are most wrong.

That is the mechanism behind "the sports we are tracking are not calibrating based on
signal". It is not that the other sports are harder. It is that **the ranking function
that decides which plays reach a card is a function fitted to baseball**, and in the
other five sports it is actively sorting the wrong way.

Over the **last 60 days** — the NFL/NBA/NHL/NCAAB era — *every* conviction bucket is
negative, including 70–79 (−5.7%) and 60–64 (−2.0%). The signal has not weakened; the
sport mix changed and the calibration went with it.

---

## 2. Where the money actually goes

### By surface, last 30 days

| surface | record | hit | implied | edge | units | ROI |
|---|---|---|---|---|---|---|
| **fade** | 77-62 | 55.4% | 52.1% | **+3.3pp** | **+7.79u** | **+5.6%** |
| sharp_card | 51-43 | 54.3% | 53.7% | +0.6pp | +1.43u | +1.5% |
| game_read | 790-575 | 57.9% | 56.2% | +1.6pp | −25.79u | −2.2% |
| ledger | 48-60 | 44.4% | 45.2% | −0.8pp | −3.77u | −3.5% |
| **sweat_card** | 75-80 | **48.4%** | 53.5% | −5.1pp | −17.26u | **−11.3%** |
| **prop_jerry** | 430-496 | 46.4% | 53.0% | −6.6pp | **−116.32u** | **−12.6%** |
| **potd** | 12-15 | **44.4%** | 55.2% | **−10.7pp** | −5.69u | **−21.1%** |
| dawg | 7-13 | 35.0% | 43.6% | −8.6pp | −5.21u | −26.1% |

**The one surface that makes money is the one that bets against our own read.** `fade`
is +5.6%; NCAAF `fade` is **+20.5% (n=54)**. We are measurably better at identifying
what not to back than what to back.

### By price band, all surfaces, last 60 days

| band | n | hit | implied | edge | units | ROI |
|---|---|---|---|---|---|---|
| dog ≥ +150 | 147 | 26.5% | 32.2% | −5.7pp | −27.16u | **−18.5%** |
| dog +100..+149 | 835 | 44.8% | 46.4% | −1.6pp | −28.32u | −3.4% |
| **pick −119..+99** | **3204** | 50.7% | 52.6% | −2.0pp | **−119.59u** | −3.7% |
| **fav −120..−199** | **1313** | 55.9% | 59.0% | −3.1pp | **−69.52u** | −5.3% |
| fav −200..−299 | 250 | 71.6% | 70.2% | +1.4pp | +4.80u | **+1.9%** |
| fav −300..−599 | 66 | 83.3% | 79.6% | +3.7pp | +3.02u | **+4.6%** |
| chalk ≤ −600 | 37 | 83.8% | 92.5% | −8.7pp | −3.66u | −9.9% |

**Heavy favourites are not the problem — they are the only profitable band.** The loss
sits in the coin-flip zone and the modest favourite, which is also where almost all the
volume is (4,517 of 5,852 priced picks).

Plus money is a consistent leak on every *curated* surface: `sweat_card` dogs
**−22.8% (n=45, edge −10.0pp)**, `sharp_card` dogs **−30.4% (n=49, edge −14.2pp)**,
`game_read` big dogs **−38.2% (n=50)**, NBA dogs −29.8%, NHL big dogs −29.9%,
`dotd` −39.6%. Strip the dog band out of the Sweat Card and it is roughly break-even.

### The two biggest single holes

1. **Non-MLB props: −173.77u on n=3307 (−5.3%, edge −2.9pp).** MLB props over the same
   history are −0.3% on n=6457 — break-even. Props are an MLB-only competence that we
   have extended to five sports where it does not hold.
2. **NFL in the coin-flip band: −117.99u on n=1801 (−6.6%, edge −3.5pp).** That single
   cell is 31% of all decided priced picks in 60 days.

### The trend Andy asked about, confirmed

| sport | Sep | Oct | Nov |
|---|---|---|---|
| MLB | +1.3% (n=5191) | +0.7% | — |
| NCAAF | −15.9% | +1.8% | −0.1% |
| NFL | −4.2% | −3.8% | **−9.0%** |
| NBA | — | +5.4% | **−8.4%** |
| NHL | −9.4% | −3.4% | **−13.6%** |
| NCAAB | — | — | −4.8% |

MLB converged to break-even-plus on huge volume and NCAAF recovered. **NFL, NBA and NHL
are all getting worse month over month.**

---

## 3. The answers to the three specific questions

### Money flow — yes, and it is our best family, and we barely use it

24 enabled signals (NFL 5, NCAAF 5, NCAAB 4, MLB 5, NHL 3, **NBA 1**) out of 583.
Weighted hit rate **63.5%** — second-best of any family — with **zero**
`ANTI_VALIDATED` and **zero** rows whose notes say "measured NEGATIVE". It is the
cleanest family in the library and it is 4% of it.

### Strength of schedule — no. Zero signals, in any sport.

And the data is already there:

- `ncaab_team_stats.sos` — **365/365 rows, 100% populated**, range −17.2 to +24.9
- `nfl_game_context.home_sos` / `away_sos` / `home_sos_rank` / `away_sos_rank`
- `ncaaf_game_context.home_sos` / `away_sos` / ranks
- `nba_game_context` carries opponent four-factors (`home_opp_efg_pct`, `opp_tov_pct`, …)

`signal_sources` rows referencing `sos`: **0**. (Verify the sign before building: the
2026-27 NCAAB column has Eastern Illinois hardest and South Carolina easiest, which is
plausible for November buy-games but is exactly the shape a sign error also takes.)

### Team stat matchups — yes, 38 of them, and they are the second-worst family

Weighted hit **49.3%**, 2 `ANTI_VALIDATED`. Measured individually on 852 joined NCAAB
games, both live eFG signals are worthless: `ncaab_home_shooting_edge` ATS 47.2% vs
49.4% blind (−2.2pp, −0.61 SE, n=197), ROI@−110 **−9.9%**; `ncaab_away_shooting_edge`
ATS 52.0% vs 50.6% (+1.4pp, +0.40 SE), ML ROI **−4.8%**. Negative or noise at every
threshold from 2 to 10pp. NHL has 2 matchup signals; MLB has 0.

### And the thing nobody asked about: the library is mostly trend-chasing

**`form / trend` is 240 of 583 enabled signals (41%)** — weighted hit **49.2%**, with
**25 `ANTI_VALIDATED`**. The largest family in the engine is the worst-performing one.

Unmeasured share of enabled signals (no registry row, or `n < 25`, which means
`edge_weight` hands them the 0.50 ramp prior):

| NFL | NCAAF | NCAAB | NHL | NBA | MLB |
|---|---|---|---|---|---|
| **60.0%** | 53.4% | 40.3% | 42.6% | 23.9% | **90.6%** |

---

## 4. What to do, in order

Ranked by measured units at stake, with the evidence each rests on. Items 1–3 are
defects; items 4–6 are product decisions that need Andy's call.

1. **Re-fit conviction per sport.** Measured basis: MLB 70–79 = +11.1%, non-MLB 70–79 =
   −4.7%, non-MLB 80–100 = −7.5% on n=661. This is the root cause and it is a defect,
   not a product choice — the number we print is anti-correlated with profit in five of
   six sports. Fit per sport, hold out the last 30 days, and ship behind a shadow column
   so the old and new ranking can be compared on live slates before either reaches a
   card.
2. **Make the signal bar the price, not the side baseline.** Already measured:
   `market=ml` hits 62.7% against a 59.4% implied (edge +3.4pp) and returns **−0.0%**.
   A +3.4pp win-rate edge converting to exactly zero money is the signature of
   `edge_weight`'s bar being `max(side_baseline, flat breakeven)`. Store each signal's
   own measured price breakeven in a readable column and use it. Replay before shipping
   — it changes scoring in every sport.
3. **Retire the measured-negative tail of `form / trend`.** 25 `ANTI_VALIDATED` signals
   in a 240-signal family at 49.2%. Start with the ones carrying live pick weight.
4. **Stop publishing props outside MLB, or demote them to unranked.** −173.77u on
   n=3307. MLB props are break-even; the other five sports are not.
5. **Gate plus money on the curated surfaces** (Sweat Card, The Sharp, POTD). Dogs are
   −22.8% / −30.4% / −38.2% there. Note this collides with `dawg` and `dotd`, which
   exist to publish dogs and run −26.1% and −39.6% — so the real question is whether
   those surfaces should exist in their current form.
6. **Build the SOS and money-flow signals** that do not exist yet — the two families
   with the best measured evidence and the least coverage. Shadow-measure first; an
   unmeasured signal collects the 0.50 ramp prior it has not earned.

### What not to do

Do not stop publishing. The engine is not noise: `game_read` beats its implied price by
+1.6pp on n=1,166 and heavy favourites return +1.9% and +4.6%. The problem is a ranking
function fitted to the wrong sport and a bar that ignores the price — both of which are
fixable, and neither of which requires reducing output.
