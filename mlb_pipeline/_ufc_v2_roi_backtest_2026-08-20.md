# UFC v2 vs v1 — ROI Backtest with Real BFO Closing Odds

_Generated 2026-08-20T15:30:44Z_
_v2 test window: `event_date >= 2026-02-01`_ (v2's own hold-out — never trained on)
_v1 training cutoff: `2026-05-06` (random 80/20 of everything present at that date)_
_Full-window fights (with feature-build + BFO odds): **n = 184** (2026-02-07 → 2026-08-15)_
_True-OOS-for-BOTH-models window (event_date > 2026-05-06): **n = 50** (2026-05-09 → 2026-08-15)_

## Executive summary

**Critical caveat.** v1 was trained via random 80/20 split of every fight in the DB as of 2026-05-06 — so any test-window fight before that date was seen during v1 training. Comparing v1 vs v2 on the full v2 test window inflates v1 by 40+pp of ROI. The fair comparison is the n=50 sub-window where neither model saw the fights during training.

- **Full v2 window (n=184, v1 LEAKY):** v1 = 81 picks 77.8% hit / +58.1% ROI · v2 = 68 picks 57.4% hit / +13.2% ROI · **v1 wins by 45.0pp ROI**
- **True OOS both models (n=50) — FAIR:** v1 = 20 picks 40.0% hit / -20.9% ROI · v2 = 19 picks 52.6% hit / -5.8% ROI · **v2 wins by 15.1pp ROI**

## Test set

- **Full v2 test window (n=184):** 2026-02-07 to 2026-08-15. v1 saw ~80% of this during training.
- **True-OOS-for-both window (n=50):** 2026-05-09 to 2026-08-15. Neither model trained on any of these.
- **v2 retrain report test window:** 241 fights → 184 land in this backtest after feature-build + odds-join filtering. Feature drops = fighter debuts / missing career stats; odds drops = BFO gaps.
- **5-round fights (main events):** 0 (0.0% of set) — table `total_rounds_scheduled` may under-tag main events; treat as advisory.
- **Title fights (5-round + 'title' in event name):** 0
- **Top weight classes:** Unknown(27), Bantamweight(25), Heavyweight(23), Middleweight(23), Featherweight(20), Lightweight(20), Welterweight(18), Flyweight(17)

## Full v2 window — v1 vs v2 side-by-side (LEAKY for v1)

_Both models scored on the same n=184 fights. v1 fit ~80% of these during its random-split training, so its numbers here are training-set performance and NOT a valid launch signal._

### v1 winner model (LEAKY)

| Tier | n picks | Wins | Hit rate | Units staked | Units P&L | ROI |
|---|---:|---:|---:|---:|---:|---:|
| PRIME | 47 | 42 | 89.4% | 67.00 | +35.96 | +53.7% |
| STRONG | 16 | 13 | 81.2% | 32.00 | +24.98 | +78.1% |
| LEAN | 18 | 8 | 44.4% | 18.00 | +7.09 | +39.4% |
| **TOTAL** | **81** | **63** | **77.8%** | **117.00** | **+68.03** | **+58.1%** |

### v2 winner model (TRUE OOS)

| Tier | n picks | Wins | Hit rate | Units staked | Units P&L | ROI |
|---|---:|---:|---:|---:|---:|---:|
| PRIME | 35 | 24 | 68.6% | 52.00 | +0.68 | +1.3% |
| STRONG | 21 | 12 | 57.1% | 42.00 | +17.72 | +42.2% |
| LEAN | 12 | 3 | 25.0% | 12.00 | -4.41 | -36.8% |
| **TOTAL** | **68** | **39** | **57.4%** | **106.00** | **+13.98** | **+13.2%** |

## True-OOS-for-both window — v1 vs v2 (FAIR)

_Both models scored on the same n=50 fights, all event_date > 2026-05-06. Neither model saw these during training. This is the honest comparison._

### v1 winner model (TRUE OOS)

| Tier | n picks | Wins | Hit rate | Units staked | Units P&L | ROI |
|---|---:|---:|---:|---:|---:|---:|
| PRIME | 7 | 4 | 57.1% | 12.00 | -3.00 | -25.0% |
| STRONG | 6 | 3 | 50.0% | 12.00 | +1.22 | +10.2% |
| LEAN | 7 | 1 | 14.3% | 7.00 | -4.70 | -67.1% |
| **TOTAL** | **20** | **8** | **40.0%** | **31.00** | **-6.48** | **-20.9%** |

### v2 winner model (TRUE OOS)

| Tier | n picks | Wins | Hit rate | Units staked | Units P&L | ROI |
|---|---:|---:|---:|---:|---:|---:|
| PRIME | 9 | 6 | 66.7% | 15.00 | -1.33 | -8.8% |
| STRONG | 6 | 3 | 50.0% | 12.00 | +1.22 | +10.2% |
| LEAN | 4 | 1 | 25.0% | 4.00 | -1.70 | -42.5% |
| **TOTAL** | **19** | **10** | **52.6%** | **31.00** | **-1.81** | **-5.8%** |

## v2 sizing-policy comparison

### Full v2 window

| Policy | n picks | Hit rate | Units staked | Units P&L | ROI |
|---|---:|---:|---:|---:|---:|
| size_flat | 68 | 57.4% | 68.00 | +5.84 | +8.6% |
| size_prod | 68 | 57.4% | 106.00 | +13.98 | +13.2% |
| size_aggressive | 68 | 57.4% | 132.00 | +14.32 | +10.8% |
| size_kelly_lite | 68 | 57.4% | 149.64 | +15.16 | +10.1% |

### True-OOS-for-both window (n=50)

| Policy | n picks | Hit rate | Units staked | Units P&L | ROI |
|---|---:|---:|---:|---:|---:|
| size_flat | 19 | 52.6% | 19.00 | -1.31 | -6.9% |
| size_prod | 19 | 52.6% | 31.00 | -1.81 | -5.8% |
| size_aggressive | 19 | 52.6% | 38.50 | -2.47 | -6.4% |
| size_kelly_lite | 19 | 52.6% | 39.94 | -5.37 | -13.4% |

_size_flat = 1u per pick; size_prod = 2/2/1 with 0.5x halving under -180; size_aggressive = 3/2/1 with juice halving; size_kelly_lite = Kelly*5, clamped [0.25u, 3u]._

## v2 cohort breakdown — full v2 window (prod sizing)

- **Picking a market favorite** — n=34, hit=76.5%, units staked=49.00, P&L=+10.39, ROI=+21.2%
- **Picking a market underdog** — n=34, hit=38.2%, units staked=57.00, P&L=+3.59, ROI=+6.3%
- **3-round undercard** — n=68, hit=57.4%, units staked=106.00, P&L=+13.98, ROI=+13.2%
- **Method top-1 = DEC** — n=33, hit=48.5%, units staked=53.00, P&L=+0.19, ROI=+0.4%
- **Method top-1 = KO/SUB** — n=35, hit=65.7%, units staked=53.00, P&L=+13.80, ROI=+26.0%
- **Picked side @ <= -300** — n=8, hit=100.0%, units staked=8.00, P&L=+1.93, ROI=+24.2%
- **Picked side @ >= +150** — n=16, hit=37.5%, units staked=26.00, P&L=+7.25, ROI=+27.9%

### Weight-class breakdown (min 3 picks)

| Weight class | n | Hit | ROI |
|---|---:|---:|---:|
| Heavyweight | 11 | 81.8% | +83.8% |
| Unknown | 11 | 54.5% | -16.9% |
| Bantamweight | 9 | 55.6% | -49.3% |
| Lightweight | 9 | 55.6% | +26.4% |
| Flyweight | 8 | 50.0% | -7.7% |
| Featherweight | 6 | 66.7% | +11.7% |
| Middleweight | 6 | 50.0% | +22.1% |
| Welterweight | 5 | 40.0% | +12.2% |
| Women's Strawweight | 3 | 33.3% | -16.7% |

## v2 cohort breakdown — true-OOS window only (prod sizing)

- **Picking a market favorite** — n=9, hit=77.8%, units staked=14.00, P&L=+3.49, ROI=+24.9%
- **Picking a market underdog** — n=10, hit=30.0%, units staked=17.00, P&L=-5.30, ROI=-31.2%
- **3-round undercard** — n=19, hit=52.6%, units staked=31.00, P&L=-1.81, ROI=-5.8%
- **Method top-1 = DEC** — n=11, hit=45.5%, units staked=19.00, P&L=-1.77, ROI=-9.3%
- **Method top-1 = KO/SUB** — n=8, hit=62.5%, units staked=12.00, P&L=-0.04, ROI=-0.3%
- **Picked side @ <= -300** — n=3, hit=100.0%, units staked=3.00, P&L=+0.89, ROI=+29.8%
- **Picked side @ >= +150** — n=4, hit=0.0%, units staked=7.00, P&L=-7.00, ROI=-100.0%

### Weight-class breakdown (min 3 picks)

| Weight class | n | Hit | ROI |
|---|---:|---:|---:|
| Unknown | 11 | 54.5% | -16.9% |

## Recommended production configuration

- **Model:** `v2` (winner head)
- **Sizing policy:** `prod` — 2u PRIME, 2u STRONG, 1u LEAN; halve when odds < 1.556
- **Juice cap:** halve units when picked side's decimal odds < 1.556 (~-180)
- **Tier gates:** PRIME p>=0.75 & edge>=0.05; STRONG 0.65<=p<0.75 & edge>=0.10; LEAN 0.55<=p<0.65 & edge>=0.10
- **Do not** enable v2's method or distance heads based on this backtest — they were not ROI-tested here. Method still shadow only, distance stays dark (retrain-report guidance).

## Risk callouts

- **Sample size:** n=50 scored fights in the fair-OOS window — VERY thin. A single fight card can flip the sign. Do NOT declare victory until 30+ live-shadow days confirm (~15-25 additional fights depending on card density).
- **v1 leakage** confounds the full-window comparison by 40+pp of ROI. If a future reader looks at this doc and sees v1 at +50% ROI on the full window, remember: v1 was trained on those fights.
- **Symmetric-average prob used here but NOT in ufc_predict.py** — prod scores in one orientation only. Live ROI may differ by 1-3pp. Porting the symmetric-average trick to ufc_predict.py is a companion PR to any v2 swap.
- **BFO median-across-books vs single-book prod odds:** median tends to be tighter than DK/FD live. Expect ~1-2% ROI decay when a single book supplies the closing line at grading time.
- **v2 hyperparam selection** used the adjacent val window (2025-10-01 to 2026-02-01, n=155). Retrain-report calibration showed a 10pp overprint in the 0.60-0.70 band — the STRONG tier is exactly there. Watch the STRONG hit rate carefully in shadow mode.
- **Time to statistical confidence:** at ~4-6 UFC events per month * ~3-5 picks per event under these gates = 15-25 picks/month. Need ~60 picks (~3 months) before the OOS ROI has a std-error under 5pp.

## Go / no-go verdict

**SWAP_TO_V2_SHADOW** — On the fair OOS window (n=50), v2 = -5.8% ROI vs v1 = -20.9%. v2 wins by 15.1pp — call it a tie or a v2 edge, and v2 is the fresher model with a proper time-based split. Recommend swap with a 2-3 week shadow window first since the fair-OOS sample is very thin.
