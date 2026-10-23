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
- Next free ID: **B158**.

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
| **B152** | OPEN | **🔥 SCHEMA GUARD — the prevention for today's outage.** Validate column/table names against the live schema once per run and fail LOUDLY on a miss. Today every dead query (`anytime_td`, `home_qb`, `ncaaf_pipeline_props`, `kickoff_utc`, `close_locked_at`, `close_spread_*_price`) was swallowed by `if r.status_code != 200: return []` and the step exited green. Fifth occurrence of that class. |
| **B153** | OPEN | **Stop swallowing non-200s.** `if r.status_code != 200: return []` must log status + URL. A 400 is a bug in our code, not a quiet day. Paired with B152. |
| **B154** | OPEN | **Request budget in the nightly.** Daily request count + 4xx rate, loud failure above threshold. Today the FIRST signal that we were issuing 214k requests/day was the app going down for paying users. |
| **B142** | OPEN | **🔥 LIVE: a published pick with no line and no price.** Receipt 3278802 (NBA game_read, NOP @ UTA tonight) is `pick_label: 'Over'`, `pick_line: null`, `pick_odds: null` — a user cannot act on it. Forward-dated NBA reads have no price at all: 3/12 today, **8/8** on 10-24, **5/5** on 10-25, with conviction compressed to 50-54 vs 52-62 on graded days. Wrong data in front of users, so it leads. |
| **B144** | OPEN | **🔥 We publish against our own read, silently.** 237 of 1,754 `game_read` receipts publish a **different market** than the `jerry_reads` row they cite; **64 publish the opposite side** of the same market (NCAAF 34, NHL 14, MLB 8, NFL 7). Last night's Bears game is one: read said CHI −1.5 and **won**, receipt said NE +2.5 and **lost**. Join verified sound (game_id 1754/1754, 1:1). The override is *not* losing (54.1% vs the read's 48.3%, n=61, CI ±12.5pp) — the defect is that the divergence is unlabelled and the read is graded as if it were the play. |
| **B143** | OPEN | **🔥 `actual_value` is NULL on 98% of graded prop receipts** (2,458 of 2,507 since 09-23; MLB 41/986, NFL 8/1521). The user sees "Loss" and never learns what the player did. Directly undercuts "we show the receipts," which is the brand. |
| **B134** | OPEN | **🔥 NFL FULL RETRAIN + ENGINE REWEIGHT — start after Week 7 (≈Mon 2026-10-27).** Andy: *"we are in week 7 almost halfway through the season, it is time."* Four parts: (a) retrain spread/total on 2026 weeks 1–7; (b) audit **how it predicts scores** — bias and MAE on home and away points, not just margin; (c) audit the **overall engine weighting** — what each signal contributes once everything is factored in, and what each is worth alone; (d) **find the pattern in the last 6 weeks** of NFL data. Walk-forward throughout, every candidate weight measured against the **close**. |
| **B135** | OPEN | **NFL recalibration cadence.** B134 is the one-time retrain; this makes it recurring. Today: spread/total retrains **~yearly**, `apply_prop_refit` is **MLB-only**, and NFL props have **no per-signal calibration** — every edge-detected prop defaults to STRONG. We ship the same model every weekend. Blocked on B134: can't set a cadence before the first retrain shows what actually moves. |
| **B141** | OPEN | **NHL full assessment.** Same treatment as B134 and B139. Known material to start from: the engine is one-sided; three different win probabilities exist and the read quoted the hidden one; prop inputs **lose to the price**; props carry an over-bias (info-only today); the `rl` market was judged not viable on merits; records were 100% preseason until fixed. Decide per market what we publish — and what we stop publishing. |
| B129 | OPEN | **Vault match dive** — status and update. How many patterns, match rate per sport on the forward slate, graded record with n, whether any pattern moves a tier today, whether any is a restatement of another. |
| **B138** | OPEN | **Prop Jerry deep dive** — performance since inception, by sport / market / tier, and what actually improves it. |
| **B157** | OPEN | **AUDIT EVERY SCRIPT FOR "WRITTEN BUT NEVER SCHEDULED."** Four found in one day: `nba_player_log_ingest.py` (root of B155 — no NBA prop grading possible), `split_sharp_card_record.py` (Sharp breakdown frozen a month in front of users), `refresh_calibration_notes.py` (same, caught 10-21), and `cross_sport_lines.yml` (workflow_dispatch only — the Fade’s only input). Each ran once by hand, then silently never again. Build the scheduling equivalent of `schema_guard --audit`: list every `mlb_pipeline/*.py` that writes to the DB and is referenced by no workflow. |
| **B155** | WIP | **NBA prop scoring — root cause found and the top link fixed.** The chain: `nba_player_log_ingest.py` was in NO workflow → zero player game logs for played games → nothing to grade → **0 graded props of 7,500** → no calibration → tier hardcoded `COVERAGE` (`nba_generate_props.py:209`) → `v_nba_props_publishable` returns 0 → The Sharp has no NBA props. **DONE:** ingest scheduled in `nba_pipeline.yml` after the resolver, and backfilled 367 player-games from 16 box scores (10-20: 68, 10-21: 254, 10-22: 45). Conviction already exists on 100% of rows with real inputs (`projection`, `proj_vs_line_edge`, `player_l5/l10_hit_count`, `minutes_played`, `lineup_state`), so the scorer is NOT missing — only the tier mapping. **NEXT:** build the NBA prop grader (writes `result`/`final_value` from `nba_player_game_logs`), let outcomes accumulate, THEN calibrate tiers against the line. Do not assign tiers before that — that is exactly how NFL ended up defaulting every edge-detected prop to STRONG. |
| **B156** | **DONE 10/23** (`773abcc3` then `1091e576`) | **Football spreads on The Sharp — BANDED, not banned.** The first pass cut all football `rl`/`spread` (NFL rl 2-11-1, NCAAF rl 16-19, −9.72u on n=49). Andy caught the scope: that would have taken NFL **dark** on 3 of the last 4 Sundays (10-04 5→0, 10-11 7→0, 10-18 2→0). Re-measured on all 435 published football spread picks and the market splits on line magnitude: **|line| ≤ 7 flipped is 66.0% n=103 CI[56.4,74.4]** (and **81.8% n=22** at tier STRONG, which is all this surface publishes), **|line| > 7 is 49.0% n=253** — a coin flip. Long stays banned; short is **flipped onto the other side** and published, priced from a genuinely posted `line_history` quote (2+ books on the exact flipped line, else the pick is dropped). Flip runs last so the no-edge and LR-shadow gates still judge the engine’s own pick. Grader needed no change — verified it reads the receipt’s `pick_side`/`pick_line`. |
| **B139** | OPEN | **The Sharp: answered 10-23, and it is NOT selection.** Caps are not binding — NBA cap 3 produced 2, NHL cap 3 produced 0, NCAAF cap 5 produced 0, global cap 20 produced 3. The surface is starved because its only profitable input (MLB props) is ending and no replacement can exist until **B155**. Record now reconciles: 543-402-3 overall = sides 236-202-3 (−18.02u) + props 307-200-0 (+54.79u). Ledger thinness is the same cause, same candidate pool. **Blocked on B155.** |
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
| **B148** | OPEN | **The Fade has no monitoring, so "quiet" and "broken" are indistinguishable.** Checked 10-23: 0 plays at 16:13 UTC was *too early*, not an outage — fade receipts normally publish 19:49-20:27 UTC, because tonight's games have no public splits at midday. Two real smells found while checking: `generate_the_fade.latest_public_splits()` takes a `date` and **never filters on it**, so its "not on this slate: 2089" counts every key it ever saw; and `archive_public_splits.SUPPORTED_SPORTS` omits **NBA** (the per-sport `write_line_snapshot.py --sport NBA` call covers it, but the archive the pattern miner reads does not). Needs an alert on the surface with our only measured edge. |
| **B158** | OPEN | **Does the short-spread flip hold forward, and how much supply does the price gate cost?** The 81.8% is n=22. Two things to watch weekly: (a) the flipped record on its own receipts, and (b) how often a short spread is dropped for “no 2+ books post the flipped line” — that fired on 1 of 2 candidates on day one (NC State @ Stanford, board moved off +6.5), which is a conservative bias but real lost supply. If it is costing most of the band, consider pricing the flip at the CURRENT posted line instead of our stale one — a different bet, so it needs its own measurement. |
| **B159** | OPEN | **Band the long football spread before leaving it banned.** |line| > 7 is 49.0% n=253 as one bucket, but 7.5-14 and 14+ were never split, and `project_publish_layer_beats_projection_1010` found our projection 15.8pts light specifically on 14+ favourites. There may be a flip inside the long band that the single bucket averages away. |
| **B151** | **DONE 10/23** (`0fde5772`) | **The Fade was validated on SPREADS and shipped TOTALS — fixed by evidence, per (sport, market).** Re-measured the live feed: football spreads **+13.4% n=96** (the edge, reproduced), **MLB totals −11.7% n=281** and **NCAAF totals −3.4% n=170** — both now BANNED; NBA/NHL totals are **unmeasured (n=6, n=10)** and stay as labelled `tracking`, because banning on n=6 mirrors publishing on n=6. Pooled totals read −8.4% n=467 but MLB+NCAAF carry it and money flow inverts by sport. Every play now carries `evidence: validated|tracking`. 10-17 Saturday: 43 plays → 16, all validated NCAAF spreads. Premise was also partly calendar — fade receipts start 10-19 and that week had no Saturday slate. |
| **B160** | OPEN | **`nfl_game_results` and `nfl_game_context` share ZERO `game_id`s** — 272 each, 0 overlap (`project_nfl_game_id_mismatch_911`, still live). `grade_game_market_receipts` grades NFL off `nfl_game_results` keyed on `game_id`, while NFL picks are built from `nfl_game_context` (md5 hash ids) — results use `20261213_ATL_CLE` form. NFL receipts *do* currently show graded (32 game_read, 2 sharp_card), so some other path is filling them: **find out which, and confirm it covers the Sharp’s flipped NFL spreads**, or they ship unmeasurable. Also blocked the NFL arm of the B151 re-measurement (13 qualifying splits, 0 gradeable). No alias column exists on either table — the bridge has to be (game_date, home_team, away_team). |
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
