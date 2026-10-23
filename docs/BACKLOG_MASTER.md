# MASTER BACKLOG

**This is the list. Add to the bottom of a tier. We work down from the top.**

`docs/BACKLOG.md` stays as the deep archive — 5,600 lines of reasoning, root
causes and measurements for every item ever opened. It is the *why*. This
file is the *what next*, and it fits on a screen.

---

## How to use it

- **Add** a line to the bottom of the tier you want it in. ID, one sentence,
  that is enough. The investigation belongs in BACKLOG.md once it starts.
- **Status**: `OPEN` · `WIP` · `BLOCKED` (say on what) · `DONE` (date) ·
  `WONTFIX` (say why).
- **Tiers** are about order, not importance. T1 is what we do next.
- An item that is **wrong data in front of users** jumps to T1 regardless of
  effort. An item that is **a claim we cannot support** is the same thing.
- Next free ID: **B151**.

---

## THE STANDING GOAL

> "Our business is to continuously find a way to predict games in all live
> sports based on data synthesis and pattern recognition across various
> factors — and not just say it's all noise." — Andy, 2026-10-22

Read that before adding anything. A null result is a finding about **one
market**, not permission to stop. The fade (+12.4% on spreads, n=107, holds
in both halves) is proof the search works when it is run properly.

Three rules that come from losing money, and they do not contradict the
above — they are how the search stays honest enough to find something:

1. **Measure against the LINE, not the outcome.** Predicting a game and
   beating the market are different problems; only the second pays.
2. **Hold out before shipping a weight.** The QB `environment` factor looked
   reasonable, shipped, and cost 3 yards of MAE until it was ablated.
3. **Walk forward.** Fitting on the weeks you are projecting is the same bug
   as being unable to backtest at all. It produced every fake 65-79% ATS
   number we have ever had to retract.

---

## T1 — NEXT

| ID | Status | Item |
|----|--------|------|
| **B142** | OPEN | **🔥 LIVE: a published pick with no line and no price.** Receipt 3278802 (NBA game_read, NOP @ UTA tonight) is `pick_label: 'Over'`, `pick_line: null`, `pick_odds: null` — a user cannot act on it. Forward-dated NBA reads have no price at all: 3/12 today, **8/8** on 10-24, **5/5** on 10-25, with conviction compressed to 50-54 vs 52-62 on graded days. Wrong data in front of users, so it leads. |
| **B144** | OPEN | **🔥 We publish against our own read, silently.** 237 of 1,754 `game_read` receipts publish a **different market** than the `jerry_reads` row they cite; **64 publish the opposite side** of the same market (NCAAF 34, NHL 14, MLB 8, NFL 7). Last night's Bears game is one: read said CHI −1.5 and **won**, receipt said NE +2.5 and **lost**. Join verified sound (game_id 1754/1754, 1:1). The override is *not* losing (54.1% vs the read's 48.3%, n=61, CI ±12.5pp) — the defect is that the divergence is unlabelled and the read is graded as if it were the play. |
| **B143** | OPEN | **🔥 `actual_value` is NULL on 98% of graded prop receipts** (2,458 of 2,507 since 09-23; MLB 41/986, NFL 8/1521). The user sees "Loss" and never learns what the player did. Directly undercuts "we show the receipts," which is the brand. |
| **B134** | OPEN | **🔥 NFL FULL RETRAIN + ENGINE REWEIGHT — start after Week 7 (≈Mon 2026-10-27).** Andy: *"we are in week 7 almost halfway through the season, it is time."* Four parts: (a) retrain spread/total on 2026 weeks 1–7; (b) audit **how it predicts scores** — bias and MAE on home and away points, not just margin; (c) audit the **overall engine weighting** — what each signal contributes once everything is factored in, and what each is worth alone; (d) **find the pattern in the last 6 weeks** of NFL data. Walk-forward throughout, every candidate weight measured against the **close**. |
| **B135** | OPEN | **NFL recalibration cadence.** B134 is the one-time retrain; this makes it recurring. Today: spread/total retrains **~yearly**, `apply_prop_refit` is **MLB-only**, and NFL props have **no per-signal calibration** — every edge-detected prop defaults to STRONG. We ship the same model every weekend. Blocked on B134: can't set a cadence before the first retrain shows what actually moves. |
| **B141** | OPEN | **NHL full assessment.** Same treatment as B134 and B139. Known material to start from: the engine is one-sided; three different win probabilities exist and the read quoted the hidden one; prop inputs **lose to the price**; props carry an over-bias (info-only today); the `rl` market was judged not viable on merits; records were 100% preseason until fixed. Decide per market what we publish — and what we stop publishing. |
| B129 | OPEN | **Vault match dive** — status and update. How many patterns, match rate per sport on the forward slate, graded record with n, whether any pattern moves a tier today, whether any is a restatement of another. |
| **B138** | OPEN | **Prop Jerry deep dive** — performance since inception, by sport / market / tier, and what actually improves it. |
| **B139** | OPEN | **The Sharp: are we picking the right plays?** Partly answered 10-23 and the answer is no. Lifetime 374-283-6 / +7.91u, but **every profitable unit is MLB** (+26.98u) and MLB ends this week; non-MLB is 55-66-1 / **−19.07u** (NCAAF −8.71, NHL −6.52, NFL 7-15-1 −5.60). By market `rl` **−11.38u**; by tier STRONG **−7.38u** vs PRIME +16.61u. Sept +26.13u → Oct **−18.22u**. Also **B146**: the "15-pick slate" has published 1-11 plays a day for two weeks, once just a single play. Decide what the surface is without MLB. |
| B131 | OPEN | **NCAAF v3 == SP+** byte-identical on 307/307 points and totals. One opinion presented as two. Modelling fix, not display. |
| B5 | OPEN | **`expo-updates` / OTA.** Every client fix is days away; the App State fix missed the 1.0.4 cut by an hour. Highest-leverage infra item. |

## T2 — SOON

| ID | Status | Item |
|----|--------|------|
| **B136** | OPEN | **NBA player projections + predicted score from that data**, then roll the same structure to NFL. Needs minutes, blowout risk, pace, injury — Andy flagged late-game minutes as the hard part. Read B134(b) first: predicted score is the same problem in both sports. |
| **B137** | OPEN | **Clean up game-prop presentation in NFL game detail**, and **add game props to NHL + NBA game detail** (the props for that game). |
| B130 | OPEN | **MLB + NCAAB records still count every phase.** MLB hashed game_id joins to no feed; NCAAB has no fixed games-per-team to verify a fix against. Needs a phase feed joined on something other than a team name. |
| B122 | OPEN | **~65 `date.today()` call sites.** DST flips **2026-11-01** — ten days out. |
| B126 | OPEN | **NFL positional defense into the prop scorer.** The table is populated and the projection layer proved it carries signal (opponent factor −2.4 MAE, the only factor that helped). Needs shrinkage + grading before weight. Feeds B134(c). |
| B124 | OPEN | Clear NBA's "fix is in review" banner when 1.0.4 is approved. |
| **B145** | OPEN | **`jerry_reads.call_line` is still off-market after the 10-19 fix** — 10-22 Bears read carries CHI −1.5 against a close of 3.0. `project_jerry_published_fake_lines_1019` fixed the published side; the read's own line was not covered. |
| **B148** | OPEN | **The Fade produced 0 rows today** (9 yesterday, 4 the day before). The one surface with a measured edge going quiet needs an alert, not a daily eyeball. |
| **B149** | OPEN | **Ladder has no sport after the World Series.** Rungs 52-63 are all MLB. Decide the post-MLB sport and price band before the streak (currently 4) dies of neglect. |
| **B150** | OPEN | **Ledger tier drift** — the Brewers ML leg is `STRONG` in `ledger_suggestions` and `PRIME` in `ledger_snapshots`, same price, same conviction 79. One of the two is wrong on every parlay. |

## T3 — QUEUED

| ID | Status | Item |
|----|--------|------|
| B132 | OPEN | 3 NBA result rows carry a name-keyed `game_id` (all Clippers) — find the fallback writer. |
| B133 | OPEN | UFC externals ungraded; B67 established the blocker is a foreign id space. |
| B127 | OPEN | FADE prop receipts show the side we did not take (282 graded on it). Andy: leave the record, stop it recurring. |
| B128 | OPEN | Badge vs game-detail read disagree on 76 games; NFL week-lock residual. |
| **B140** | OPEN | Add a one-line status index to `docs/BACKLOG.md` so closed and open items are distinguishable without reading prose. |
| **B147** | OPEN | (hygiene) `public_receipts.result` holds both `WIN` and `Win` — 1,606 Title-case rows, nearly all `prop_jerry`. Not user-facing: `compute_surface_records._classify()` lowercases both sides. But it has now produced a false audit reading twice, so normalise it at the writer. |

## BLOCKED

| ID | Blocked on | Item |
|----|-----------|------|
| B135 | B134 | can't set a cadence before the first retrain shows what moves |
| B130 | a phase feed | MLB / NCAAB records |
| B133 | id-space bridge | UFC externals |

---

## RECENTLY DONE — 2026-10-22

Kept short, and only to stop re-opening them. Detail is in BACKLOG.md and
`docs/daily/2026-10-22.md`.

- **NBA records** exactly 82/team, 1,230/season, both seasons (`20261022d/f`)
- **NHL records** drop the playoffs — the game id already encoded it (`20261022g`)
- **NBA external picks graded for the first time ever** — 0 → 60 track-record
  rows. Third occurrence of that defect; the caller audit is now in code.
- **NBA externals unblocked** — `nba_pipeline.yml` never installed playwright,
  so 4 of 6 sources failed ~10 of 11 runs
- **CBS expert picks** added for all six sports (server-rendered, no browser)
- **Abbreviation matching** — OKC/GSW/NYK/SAS/LAL are not substrings of their
  team names; every abbreviation-publishing source was losing those games
- **NCAAF slate 16 → 76** — one malformed game_id rejected an atomic batch
  and the function printed a checkmark on zero writes
- **v3 sign convention** normalised on all three surfaces (two were fixed
  earlier in the day, the Model Consensus grid was the third)
- **NFL player projections** QB + RB/WR/TE, 189 rows, in the nightly
- **Injury feed** — 21% of `reason` values were news blurbs; stale-scrape expiry
- **Daily Degen** printed raw column names on every non-baseball leg
- Four Factors off · Numbers moved · low-conviction blurb removed · QB card

### Measured, and recorded as findings rather than features

These are the starting inputs for B134, not reasons to skip it.

- QB projection vs the LINE: slope **+0.077** — no information past the market
- A league-average constant beat our QB baseline (63.7 vs 64.9)
- `environment` factor was **hurting** (+2.3 solo, −3.0 to remove) — disabled
- Shrinkage K=2 held out across three folds (+3.4, +7.6, +0.8) → 64.9 → 60.9
- **Anytime TD conviction is inverted** (76+ went 1-12, −72.7% ROI) because
  `projection` is an expected COUNT read as a probability
- **NBA v3 and MC are not duplicates** (identical on 0 of 69) — unlike NCAAF
