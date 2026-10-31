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
- Next free ID: **B197**. (R-series: next free **R42**.)

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
| **B157** | **DONE 10/23** (`31ef6ed9`) | **AUDIT EVERY SCRIPT FOR "WRITTEN BUT NEVER SCHEDULED."** Four found in one day: `nba_player_log_ingest.py` (root of B155 — no NBA prop grading possible), `split_sharp_card_record.py` (Sharp breakdown frozen a month in front of users), `refresh_calibration_notes.py` (same, caught 10-21), and `cross_sport_lines.yml` (workflow_dispatch only — the Fade’s only input). Each ran once by hand, then silently never again. Build the scheduling equivalent of `schema_guard --audit`: list every `mlb_pipeline/*.py` that writes to the DB and is referenced by no workflow. | **Shipped:** `audit_unscheduled_writers.py` — reachability-aware (workflow name OR transitive import), in the nightly as a detector. 412 writers scanned, 475 reachable, **54 need a decision**, 37 one-shot by name. Triage of the 54 is the follow-up, tracked at B167.
| **B155** | WIP | **NBA prop scoring — root cause found and the top link fixed.** The chain: `nba_player_log_ingest.py` was in NO workflow → zero player game logs for played games → nothing to grade → **0 graded props of 7,500** → no calibration → tier hardcoded `COVERAGE` (`nba_generate_props.py:209`) → `v_nba_props_publishable` returns 0 → The Sharp has no NBA props. **DONE:** ingest scheduled in `nba_pipeline.yml` after the resolver, and backfilled 367 player-games from 16 box scores (10-20: 68, 10-21: 254, 10-22: 45). Conviction already exists on 100% of rows with real inputs (`projection`, `proj_vs_line_edge`, `player_l5/l10_hit_count`, `minutes_played`, `lineup_state`), so the scorer is NOT missing — only the tier mapping. **NEXT:** build the NBA prop grader (writes `result`/`final_value` from `nba_player_game_logs`), let outcomes accumulate, THEN calibrate tiers against the line. Do not assign tiers before that — that is exactly how NFL ended up defaulting every edge-detected prop to STRONG. |
| **B156** | **DONE 10/23** (`773abcc3` then `1091e576`) | **Football spreads on The Sharp — BANDED, not banned.** The first pass cut all football `rl`/`spread` (NFL rl 2-11-1, NCAAF rl 16-19, −9.72u on n=49). Andy caught the scope: that would have taken NFL **dark** on 3 of the last 4 Sundays (10-04 5→0, 10-11 7→0, 10-18 2→0). Re-measured on all 435 published football spread picks and the market splits on line magnitude: **|line| ≤ 7 flipped is 66.0% n=103 CI[56.4,74.4]** (and **81.8% n=22** at tier STRONG, which is all this surface publishes), **|line| > 7 is 49.0% n=253**. **SUPERSEDED 10-24:** that >7 figure pooled a real edge at 8.5–10.5 with noise at 13.5+. Walking the cutoff, the flip clears breakeven all the way to **|line| 10.5 (62.0% n=121, CI[53.1,70.1])** and peaks at ≤7 (71.0% n=69). Band is now 10.5 in `MAX_MAGNITUDE_BY_SPORT`. Past 10.5 stays banned; short is **flipped onto the other side** and published, priced from a genuinely posted `line_history` quote (2+ books on the exact flipped line, else the pick is dropped). Flip runs last so the no-edge and LR-shadow gates still judge the engine’s own pick. Grader needed no change — verified it reads the receipt’s `pick_side`/`pick_line`. |
| **B139** | OPEN | **The Sharp: answered 10-23, and it is NOT selection.** Caps are not binding — NBA cap 3 produced 2, NHL cap 3 produced 0, NCAAF cap 5 produced 0, global cap 20 produced 3. The surface is starved because its only profitable input (MLB props) is ending and no replacement can exist until **B155**. Record reconciles, and on **10-24 the basis changed back**: lifetime **531-384-10, +81.72u**, sides+props MATCH on every window (`SHARP_LEGACY_FREEZE` restored to True — see B174). The 543-402-3 / +36.77u figure was the receipt-only basis that shipped for one day. Ledger thinness is the same cause, same candidate pool. **Blocked on B155.** |
| B131 | OPEN | **NCAAF v3 == SP+** byte-identical on 307/307 points and totals. One opinion presented as two. Modelling fix, not display. |
| **B180** | OPEN · **TEMPORARY, REMOVE AFTER REVIEW** | **A 06:00 UTC Sunday cron was added to `ncaaf_pipeline.yml` so Saturday 10-24 grades overnight** instead of waiting for the 13:00 UTC (9am ET) resolver. The last Saturday kickoff is 10:15pm ET and finishes ~1:30am, so 2am ET is after the window and seven hours earlier than the standing job. Same job, run once, no extra request volume. A matching `DOW=7 HOUR=06 -> resolver_only` case was added to the mode detector, because without it the slot falls through to `mode=full` and runs the entire pipeline at 2am. **Delete both once the weekend is reviewed.** |
| **B181** | OPEN | **3 forward receipts were frozen on the OPPOSITE side of their pick and had to be voided** (LA@LV `LV +6.5` vs pick `LA -5.5`; DAL@PHI `PHI ML` vs `DAL -4.5`; CAR@GB `GB -7` vs `CAR +3`). All 27 forward-dated NFL `game_read` receipts came from ONE batch on 10-13/14/17, written 12 days ahead, before `backfill_public_receipts.py` gained its future-date skip — so the guard is working and this is legacy, not an ongoing leak. The remaining 24 still agree with their pick. **What is NOT fixed:** `pick_side`/`pick_line` are immutable, so a receipt frozen on a stale side can only ever be voided, never corrected. Any future early write has the same one-way door. Worth asserting at write time that the game is within the lock window, not merely not-future. |
| **B185** | OPEN | **Sleeper pull: what we take, what they actually have, and what we DO with it.** Andy: *"i know whatw e oull what they ahve and what we use their data for currently"*. Two pullers exist and both are scheduled — `nfl_sleeper_projections_pull.py` (`nfl_pipeline.yml`) and `nba_sleeper_projections_pull.py` (`nba_pipeline.yml`) — storing per-player projections: `proj_pass_yds/tds/ints/attempts`, `proj_rush_yds/tds/attempts`, `proj_rec_yds/tds`, plus `proj_targets`. Known consumers: `nfl_panel_projection.py` (Sleeper is the PRIMARY source, ESPN the fallback) and `nfl_player_projection.py`. **Three questions to answer with measurement, not a read of the code:** (a) **what are we leaving on the table** — Sleeper's API also carries injury designations, depth-chart order, trending add/drop and snap-adjacent usage we may not be pulling at all, and the depth chart matters because `nfl_current_depth_chart.json` is the stale file behind B161 and the QB-staleness problem; (b) **is their projection any good** — `nfl_player_projection.py:322` already notes a comparison against "sleeper's 61.5" on a thin 85-game basis, so measure Sleeper's projection against the LINE per market, the same test that found our own QB projection barely beats a constant; (c) **`proj_targets` is pulled and consumed by nothing** (see B184), so at least one field is already dead weight. Feeds B136 (NBA player projections) and B183. |
| **B188** | **DONE 10/25** | **The 10-24 NCAAF records now grade the side we published.** Audited all three remaining surfaces against the final scores, then void+reconstructed under Andy's authorisation. **sweat_card: 3 fixed** (BYU -7.5/Iowa -7.5/Indiana -7.5 held the pre-flip side while the card displayed UCF/Minnesota/Michigan +7.5, all three WINS) -> d30 **54.7% -> 56.5%**, and ROI flipped sign **-1.22% -> +2.32%** (-1.96u -> +3.74u). **game_read: 14 fixed** -> **27-17 -> 24-20**, i.e. correcting it made the record WORSE because the flips went both ways (5 LOSS->WIN, 8 WIN->LOSS, 1 unchanged). Cross-check passed: the corrected receipts equal an independent grading of all 44 published `call_text` values (24-20 both ways, two code paths). **sharp_card: nothing to fix** (the 3 flagged rows are deliberate voids; void != LOSS). **fade: nothing to fix** -- 0 wrong side, 0 wrong grade, 7-3; the "4-6 displayed" in the original report is a DISPLAY bug, so that part moves to a UI item, not a data one. Unblocked by migration **20261025a** (a voided receipt kept its unique key, so void+republish was impossible until the constraint became a partial index over live rows). Two schema facts found en route: `source_id` is NOT NULL and `capture_mode` is CHECK-constrained to ('live','reconstructed'). **Root cause B171 is still open** -- this will recur next Saturday. |
| **B186** | **DONE 10/25** | **🔥 The Sweat Card recap left WINNERS stuck Pending forever, and under-reported the day.** Andy: *"record reads 2-1 and should be 5-1"*. He was right. `_resolved_result()` matched a top_8 leg to its grade via `receipt_lookup[label.lower()]` — a pure LABEL match. The card displayed `UCF +7.5`, the only receipt on that game said `BYU -7.5` (the pre-flip side), no match, so the leg returned `null` and sat **Pending permanently**. Three winners invisible: UCF +7.5, Minnesota +7.5, Michigan +7.5. Recap read **2-1 with 3 pending** on a day the card went **5-1**. **Fixed:** a game pick now grades itself from the box score when no receipt matches — parsed by SHAPE (trailing signed number is the line, text before it the team, `game` gives away/home), per [[feedback_parse_a_pick_by_shape_not_vocabulary]]. A real graded receipt still wins; this only catches what would otherwise stay Pending. Verified on all 6 of yesterday's legs before republish; recap now reads `wins 5, losses 1, pending 0`. **Note this is a SYMPTOM of B171** — the root cause is still that the receipt holds the pre-flip side. |
| **B187** | OPEN | **`sport_registry` notes are stale, NFL worst.** Andy: *"updat prop jeerry s very stale sports registry note as it metniongs somehting about weeks 3-4"*. Verified: **NFL's row has not been updated since 2026-08-09** — two and a half months, and we are in week 7. It carries only a generic `today_note` ("Full analysis populates by Thursday morning") with **no `state_message` and no `calibration_note`**, so Prop Jerry shows no calibrated context for NFL at all while NHL and UFC both carry live W-L sentences. The specific "weeks 3-4" string was NOT found in `sport_registry` text, the app `.tsx`, or `refresh_calibration_notes.py` — so locate it first (candidates: a prop-tab note, a frozen `input_snapshot`, or hardcoded copy) rather than assume. While there: `refresh_calibration_notes.py` already auto-generates these from `surface_records`, so the fix is probably scheduling/coverage, not writing new copy. |
| **B182** | OPEN | **📊 PROP GRAPH REBUILD — Andy supplied a reference design.** Bar per game with the VALUE labelled on each bar, hit/miss colour-coded against the posted line, the line itself as a labelled dashed reference (`O 92.5`), the two most recent games called out with a darker chip, and a summary strip underneath: **SZN / H2H / L5 / L10 / PREV**, each showing hit% AND the average in that window (e.g. L10 `20%` over `62.50`). Dates + opponent on the x-axis with `@` for away. Today's graph shows less and reads worse. Pairs with B138 (Prop Jerry dive) and the RUNBOOK at [[feedback_prop_graph_rendering_912]]. **Client work — needs a build, so it rides 1.0.5+.** |
| **B183** | OPEN | **🔥 PROPS HAVE NO OPPONENT LINE, AND THE TWO WORST FAMILIES ARE THE TWO WITH NO USAGE DATA.** Andy: *"factor in opponent stats line for the prop market type so says qb rushing eyards defense rushing yards allowed by qb"*. Measured over 1,247 graded props: usage metrics attach to RECEIVING props at 98% coverage and **0% on rushing, 0% on passing** — and `rush_yds` runs **41.2% n=68**, `pass_yds` **41.5% n=65**, the two worst families in the window. Every prop needs the opponent mirror of its OWN stat: QB rush yds vs rush yds allowed **to QBs**, WR rec yds vs pass yds allowed **to WRs**, RB rush yds vs rush yds allowed **to RBs**. `compute_nfl_positional_defense.py` already exists and B126 says the opponent factor was the ONLY factor that helped the QB projection (−2.4 MAE). Also wire REGRESSION into the graph + score: a player far above his season average is a fade candidate, the same concept the engine lacks on sides (B178, [[project_engine_cannot_reason_about_regression_1023]]). |
| **B184** | **RETRACTED 10/25** | **The 70% `l4_targets` finding does not replicate.** The premise was `l4_targets` 9+ at 70.0% CI[52.1,83.3] on the 1,247-prop window. Re-measured over 3,399 graded NFL props since 09-19: **9+ is 49.4% n=162**, and the whole ladder is flat (0-2 49.9%, 3-5 49.3%, 6-8 45.9%). `l4_wopr` is flat too (47.6-49.5% across every bucket). So usage does NOT carry the dose-response the original window suggested, and wiring the 9+ threshold into the scorer would have encoded noise. Keep `proj_targets` unused. The real signals are in **B193**. |
| **B178** | OPEN | **🔥🔥 TIER IS PREDICTIVE, AND WE APPLY IT BACKWARDS ON BOTH BANDS.** Measured on 402 graded football spread receipts, tier joined from `primary_play` (see B179 for why not the receipt), split-half validated: **NCAAF >10.5 STRONG+ = 59.5% n=74, ROI +13.5%, and it HOLDS in both halves (56.8% early / 62.2% late)** — the only cell that does. Same band at LEAN/COV/PASS is 45.9% n=122. But **≤10.5 STRONG+ is 34.4% n=61, BELOW breakeven and worsening (25.8% late)** while ≤10.5 LEAN/COV/PASS is 42.2%. Pooled, the two cancel to "STRONG 48.9% vs LEAN 46.6%", which is why this read as noise until it was banded. **Consequences: (a) The Sharp BANS long football spreads (B156) — its best twice-validated football population, and that ban is what excluded Texas State −13.5 from Andy's 10-24 curation; un-ban >10.5 at STRONG+. (b) Keep flipping ≤10.5 STRONG+ — tier being inverted there is exactly why the flip runs ~66%. (c) Drop ≤10.5 LEAN/COV/PASS, no edge either way. (d) NFL ≤10.5 STRONG+ is 32.0% n=25 against LEAN/COV/PASS at exactly 50.0% in BOTH halves — inverted too; too little NFL long data to judge. (e) CONVICTION IS USELESS AS A RANKER: NCAAF is non-monotonic and the 65–74 bucket is the WORST at 35.3% n=51 — and we rank by it today.** Feeds B134(c) and B139. |
| **B179** | **DONE 10/24** (`8d55bf30` + `backfill_receipt_tier.py`) | **`public_receipts.tier` was EMPTY on all 327 graded `game_read` spread rows**, so the gate that decides what users see cannot be scored from the row that gets graded. The 88 STRONG/PRIME receipts all sit on `sharp_card`/`sweat_card`, which admit STRONG+ by design, so pooling surfaces compares a curated slate against every game. B178 had to join tier from `primary_play`, which is MUTABLE — a re-score after the game shows the current tier, not the published one. Exactly the same defect class as `signal_attribution.signal_side` (fixed 10-24): **the discriminator is not stored on the graded row.** Stamp tier and conviction at receipt-write time. | **Shipped:** writer now reads `input_snapshot->primary_play->>tier` (frozen at read time, immutable). That alone was NOT enough — the receipts upsert is `resolution=ignore-duplicates`, FIRST WRITE WINS, so a re-run reported "written=496" and changed nothing. `backfill_receipt_tier.py` does the explicit UPDATE: **739 rows stamped** (NCAAF 364, NFL 103, NHL 231, NBA 41) in **37 Supabase requests** by grouping ids per tier and PATCHing in chunks. Only null-tier rows are touched, so it is idempotent and cannot overwrite a real tier. Verified safe before writing: NO query anywhere filters public_receipts on tier, and the app’s tier chip was removed from that surface, so no published record moves. MLB’s 786 stay null — its reads carry no tier in the snapshot. **B178 then re-measured off the receipt instead of the mutable join and got STRONGER: >10.5 STRONG+ 60.5% n=81 ROI +15.5%, early 62.5% / late 58.5%.** |
| **B171** | OPEN | **🔥 The flip runs AFTER the receipt writer, so 32 games display one side and grade another.** `flip_football_spreads.py --over-receipts` had to be invented on 10-24 because every NCAAF game already carried a frozen `game_read` spread receipt by the time the flip ran. It is accepted only on DIRECTION — the original side is the one that went 0-5, so the record scores our worse side and understates us. Move the flip **before** receipt publication in `ncaaf_pipeline.yml` / `nfl_pipeline.yml` and the whole divergence class disappears; `--over-receipts` then becomes a break-glass flag instead of normal operation. |
| **B172** | **WIP — BLOCKED on one SQL paste** | **🔥 Nothing alerts when a PUBLISHED RECORD moves more than the day's results can explain.** On 10-23 the all-time Sharp went +81.21u → +36.77u overnight with nothing on screen explaining it; the only detector was Andy reading the card. There is **no `surface_records` history table at all**, so day-over-day movement is unmeasurable after the fact. Snapshot `surface_records` daily, then alert when `units_net` moves by more than the newly-graded picks could account for. This is the item that takes a whole class of fire off his desk.| **Built 10/24:** migration `20261024a_surface_records_history.sql` + paste-ready `_apply_20261024a_...md`; `snapshot_surface_records.py` (ET grain, merge-duplicates so an intraday change is not frozen out, refuses to write an empty snapshot); `watchdogs.check_record_discontinuity` registered in CHECKS — budget is `2.0 x new_picks + 1.0`, CRITICAL on lifetime/epoch. Snapshot step wired into `daily_card.yml` right after `compute_surface_records.py`. **Both pieces are INERT until the migration is pasted** (writer 404s; the check returns None on 404 by design so it cannot cry wolf about a missing table — which also means `--dry-run` printing `clean` today is NOT a pass). After the paste: run the snapshot once by hand, and the check goes live on the SECOND ET day, because one snapshot is not a comparison. |
| **B173** | **MEASURED 10/25 — CANNOT BE FIT YET, and here is exactly why** | **Signal weights still cannot be fitted, but the reason is now specific rather than "unblocked".** Three hard blockers found by measuring `signal_attribution` rather than assuming it was ready. **(1) SAMPLE.** Distinct GRADED (game, pick) rows: NCAAF rl **336**, NCAAF ml 132, **NFL rl 56, NFL ml 34**, NFL total 9. With ~20 enabled signals, 56 observations is under 3 per parameter -- any NFL coefficient would be overfitting, and pooling with NCAAF is the "pooled bands cancel" error. **(2) COVERAGE.** Attribution captures only **7 distinct signal keys on NFL and 10 on NCAAF**, not the ~20 enabled. You cannot weight what is not recorded, so expanding attribution coverage is a PREREQUISITE, not a follow-up. **(3) COLLINEARITY — the panel is one voice inside the signal layer.** `COHORT_NFL_HOME_FAV` points HOME on **59 of 59** rows: it is not a signal, it is a LABEL, and its "30.0% hit rate" is simply the home-favourite base rate. `LR_SHADOW` agrees with it on **90% (46/51)**, so the ensemble has been counting the home-favourite view at least twice. `OFF_RATING_GAP` is the only semi-independent voice (52% HOME, 63-71% pairwise agreement) and it measures **32.7% n=52**. **Univariate results** (graded on the SIGNAL's own side off close_line + actual_margin, so our selection never enters it): NFL `LR_SHADOW` **26.5% n=49 CI[16.2,40.3]**, `COHORT_NFL_HOME_FAV` 30.0% n=40, `OFF_RATING_GAP` 32.7% n=52 -- all three ANTI-predict with CI upper bounds below the 52.38% breakeven. On NCAAF **nothing predicts**: best is `COHORT_NCAAF_HEAVY_HOME_DOG` 63.2% n=57 but CI[50.2,74.5] does not clear. **Also found:** `close_line` is stored with an INVERTED sign on **2026-09-12** only -- all 26 NCAAF rows where the cover formula disagrees with the stored result fall on that date and the opposite sign explains 26 of 26. Exclude that date from any fit. Convention validated per sport: NFL 56/56, NCAAF 310/310. **DO THIS FIRST:** expand attribution coverage, strip `COHORT_*_HOME_FAV` out of the weighted ensemble and make it a conditioning BUCKET, de-duplicate the LR/COHORT collinearity, then accumulate n. Feeds **B190**. |
| **B174** | OPEN | **`surface_records` has no `allocated` column, and its absence rejected a 10-row write.** `split_sharp_card_record.py` stamps which buckets contain the allocated frozen residual; PGRST204 refused the whole upsert while the log printed "reconciles YES". Column dropped from the payload as a stopgap, so the estimated portion is currently only visible in run output. Add the column (migration ends with `NOTIFY pgrst`), restore the stamp — the frozen-era split is an ESTIMATE and the row should say so. |
| **B175** | **DONE 10/25** (`assert_publish_invariant.py`) | **One invariant, asserted after every publish: badge == read == card == receipt, on SIDE and MARKET.** Andy: *"why cant we get on same page with records and resolvers it always something every day"*. Built and wired into all four pipelines (NFL/NCAAF/NBA/NHL) as a hard step -- NO `continue-on-error`, placed last, because an advisory version of this check is a muted one and that is the exact mechanism that hid a dead depth-chart scrape for 39 days. **First run on the 10-25 slate found 8 real disagreements across 24 games**: NFL IND@MIN (4 surfaces say `IND +7`, the badge alone says `MIN -5`), NBA IND@BKN and NHL MIN@VAN + MTL@WPG on opposite SIDES, plus 4 MARKET-only rows and 7 line-drift warnings. **Two design decisions that make it usable rather than noisy:** (a) `fade` and `ledger` are EXCLUDED -- the Fade's thesis is to oppose public money so it is *expected* to disagree, and on the first run it generated 5 of 16 false criticals purely by doing its job; (b) it reports the MAJORITY and names the DISSENTER rather than assuming the badge is authoritative -- the first version printed four rows blaming the three *correct* surfaces on IND@MIN. `--fail-on side` by default: a SIDE mismatch fails the build, a MARKET-only one shouts, because MARKET is the known **B144** population (237 of 1,754) and a permanently red step gets ignored. Tighten to `--fail-on any` once B144 clears. Read-only by design -- it never repairs, because an automatic repair is how a wrong side becomes permanent. |
| **B176** | OPEN | **NFL has no own number: `projected_spread_raw` correlates +0.11 with actual margin (n=90), against NCAAF's +0.55 (n=336).** The anchored version scores better (+0.31) only because it imports the market. This is the direct cause of NFL curated spreads at 27.1% / −45.9% ROI — we publish off a projection with almost no signal. NCAAF by contrast runs on live current-season data (254/254 October rows) and its own number is real, though conditional on the close it adds only +0.10 CI[−0.10,+0.31]. Also: `projected_spread_source` is NULL on 215/472 NCAAF rows, `sp_plus` on 254, `epa` on **3**, and NFL has no source column at all. Belongs inside **B134**. |
| **B177** | **DONE 10/26** | (hygiene) **`MEMORY.md` trimmed back under its cap: 27,789 → 24,309 bytes.** 18 index hooks had grown into paragraphs and the tail of the index was being dropped from every session. Hooks now keep their link and lead clause only; detail stays in the topic files, which is where every one of those links already pointed. Nothing was deleted — no memory lost a row, and all 236 links verified intact. Two new entries added the same pass (`feedback_walk_forward_misses_feature_leak`, `project_prop_edge_was_the_l5_leak_1026`). |
| **B197** | OPEN | **🔥🔥 THE 9/22 L5 LEAK IS STILL IN THE STORED VALUES, and it was the only prop "edge" we had.** The fix bounded the *fetch*; it never rewrote what was already written. `_stat_avg_l5` is written at `backfill_prop_lookback.py:791` from `recent`, and on pre-fix rows `recent` came from the unbounded season gameLog — so **12,316 of 12,934 graded, priced, enriched MLB prop rows have their own game, or a LATER game, inside their stored lookback window** (deGrom 08-31 UNDER 17.5 outs carries `{"date":"2026-08-31","value":21.0}` as entry one; Rece Hinds on 09-14 carries entries dated 09-15 and 09-16). **What this invalidates:** (a) the ratio-quintile rule I was about to ship — rebuilt bounded, UNDER Q5 goes **+10.1 vs BE / ROI +14.92% → −1.5 vs BE / ROI −3.72%**, and the clean quintile ladder is FLAT (every UNDER bucket −0.4 to −1.6 vs its own breakeven); (b) **MLB tier PRIME, pre-fix 73.6% / +14.6 vs BE / ROI +23.69% n=1890 → post-fix 55.8% / −0.5 / ROI −3.51% n=163**; (c) the "conviction is monotonic 48.5→67.0%" claim, which was a RAW hit rate across the leaked window. **The honest state of the MLB prop book: −6.1% ROI pre-fix and −6.5% post-fix — we select props that lose to the closing price, and the fix did not change it.** **Repair is possible in place and needs no API calls:** `_stat_last10` stores per-game rows WITH DATES, so a bounded L5/L10/season average can be recomputed from data already on the row. **Not user-facing** — `fetchPropHistory` is a no-op stub since the BDL removal and both keys sit in the app’s `METADATA_KEYS` blocklist (`app/index.tsx:263`), so no subscriber has seen a contaminated window. Until repaired, **no analysis may read `_stat_avg_*` on pre-9/22 rows** and MLB prop tier records must not be surfaced. Method note: walk-forward printed STABLE EDGE on every contaminated cut — it cannot see feature leakage, because uniform contamination looks like stability. Audit B193’s gate the same way before shipping it. |
| **B198** | OPEN | **NHL props are completely ungated — there is no tier to be right or wrong about.** Of 12,034 graded, priced, in-band NHL props, **11,988 sit at conviction 0-49 and EVERY SINGLE ROW is tier COVERAGE**. No gate, no discrimination, one numeric signal key — 12,034 props at **−6.80% ROI**, which is the undifferentiated market price. This is the concrete "lift NHL" task and it is plumbing before modelling: the sport has volume (37,856 graded props, 3x MLB) and no selection layer at all. Do NOT assign tiers off a projection first — that is how NFL ended up defaulting every edge-detected prop to STRONG (B135) and how NBA nearly did (B155). Build the discriminator, measure it against each bucket’s own breakeven, then map tiers. Pairs with B141. |
| **B199** | OPEN | **NFL prop conviction is flat and PRIME is the WORST tier.** corr(conviction, price) = −0.002 on n=3,968 and every conviction bucket lands between −2.3 and +1.0 vs its own breakeven. Post-fix tier ordering is inverted at the top: **PRIME −5.7 vs BE / ROI −10.94% n=168** while **LIGHT is +1.9 / +3.37% n=688** and COVERAGE is −11.8 / −25.15% n=77. Same shape as `project_anytime_td_conviction_inverted_1022`. Either rebuild conviction off projection-vs-price or stop ranking by it — ranking by a flat field is what makes the top of the board look curated when it is not. Feeds B134(c). |
| **B200** | OPEN | **🔥🔥 `open_spread` IS A COPY OF `close_spread` — we hold NO line-movement data.** 1,967 of 1,967 NFL rows with both columns are identical, same for `open_total` / `close_total`, and every open-close diff is exactly 0.0. The sweep reported `no real move n=1742 50.5%` for every bucket, which is the ABSENCE of the data, not a finding about movement. **Why this is the expensive one:** B-ref `project_spread_residual_unpredictable_1018` tested 151 features against the spread residual and found exactly ONE survivor — post-publish line move, ~16 points of real information. The single most promising signal anyone has measured here, and the column that would carry it is a duplicate. `line_history` is the real source (see B-ref `project_line_history_outage_921`): either populate `open_*` from the earliest quote per game, or stop writing the columns so nothing mistakes them for openers. **Do not compute movement from `open_spread` until fixed** — it returns 0 and reads as "movement does not matter". |
| **B201** | OPEN | **NCAAF weather is 69% missing, so the one VALIDATED lens cannot ship on our biggest slate.** wind >= 12 → UNDER is validated on NFL over 28 seasons (walk-forward 55.8%, CI[52.9,58.6], 17 of 26 seasons, control side 49.3%). NCAAF has 15,655 completed games — more volume than NFL — and the lens is unusable: `ncaaf_game_results.wind` is empty on **all 6,563** games carrying a `close_total`; weather lives on `ncaaf_game_context` which has **654 rows total, wind on 200, `weather_source` NULL on 454**. Only **153** join to a graded total and only **27** reach wind >= 12, so the transfer test is **INCONCLUSIVE, not negative** — do not record "wind does not work on NCAAF", nothing was measured. Open-Meteo’s historical archive needs no key and the OPENWEATHER key is already in GH secrets (B-ref `project_weather_pullers_silent_noop_1009` — never ask Andy for it; a fail-loudly guard in front of Open-Meteo already cost NCAAF five weeks of weather). Backfilling kickoff weather turns one validated signal into a deployable one. |
| **B202** | **BLOCKED — DO NOT SHIP** (was OPEN, same day) | **The wind lens is validated on a number we cannot bet.** The 28-season result (walk-forward 55.8%, CI[52.9,58.6], 17 of 26 seasons, control 49.3%) is measured on `nfl_game_results.wind`, the **at-game reading**. Pregame we only have `nfl_game_context.wind`, an OpenWeather/Open-Meteo **forecast**, and they are not interchangeable: |forecast−actual| mean **4.84 mph** (median 3.0, p90 15.0) and a >=12 trigger agrees on only **13 of 19 (68%)** — the forecast fires on 4 games where the reading fires on 2. Graded on the forecast: **5-7, 41.7%, n=12, ROI −20.46%**, control 44.1% n=34, and the whole 46-game forecast population runs 43.5% UNDER. **n=12 is INCONCLUSIVE, not disproven** (CI[19.3,68.0]) — but it is the only evidence about the version we could act on and it points the wrong way. Two more trigger problems: the forecast is **integer-quantized** (0 of 90 non-integer) with **31% of all >=12 triggers sitting exactly ON 12.0**, so a 1 mph error moves a third of the plays; and **34 of 90 forecasts are exactly 0.0**, which smells like a dome default or a failed pull written as zero instead of NULL. **Blocked on B205.** When it unblocks, re-derive the threshold on the FORECAST and put it off a modal value. |
| **B203** | OPEN | **Our stored prop projection leans the WRONG WAY and loses to a 10-game mean.** Measured 10/26 on 2,290 graded NFL props against the only honest benchmark, the LINE: slope of `(actual-line)` on `(projection-line)` is **−0.075, CI[−0.156,+0.005]** — not merely uninformative, nearly significantly INVERTED — and its MAE is **12.583 vs 12.228 for a plain bounded 10-game mean** and **10.944 for the line itself**. Third member of this family after `project_anytime_td_conviction_inverted_1022` and `project_nfl_qb_projection_measured_1022` (a league-average constant nearly beat it). Either stop ranking props by it or rebuild it off projection-vs-price. Note a projection is not worthless — all of them beat a single constant (43.581) by ~3.5x — the problem is specifically that the market already holds everything they know. |
| **B204** | OPEN · **RESCOPES B183** | **The opponent mirror is ALREADY PRICED — B183’s premise is answered NO.** B183 proposed wiring opponent positional defense into the prop score, on the theory that `rush_yds` 41.2% and `pass_yds` 41.5% were the worst families *because* they carry 0% opponent usage data. Built the feature properly from all **160,417 player-weeks** in `nfl_player_stats` (opponent-allowed per position per stat, relative to that position’s league mean, bounded strictly before the prop’s season/week) and tested it against the line: standardised slope **+0.022, CI[−0.022,+0.065] — nothing**. Same for `target_share` (−0.015), `wopr` (−0.021), per-opportunity rate (+0.016) and recent volume (−0.000). The ONLY survivor is recent form itself (`l5-line` standardised **+0.061, CI[+0.018,+0.104]**) and at a ~0.06 correlation it does not beat the vig — best betting cut is −0.1 vs breakeven. **So do not expect positional defense to lift those families.** Rescope B183 to DISPLAY value only (users want to see the matchup) and stop treating it as a scoring fix. Two gaps found en route: `nfl_pipeline_props` stores **no opponent column** (0 of 2,047 rows — opponent had to be recovered via the player’s team in his last prior game joined to the schedule), and **1,940 of 4,301** graded NFL props have an unmapped `prop_type` (`anytime_td` 417, `pass_interceptions` 118) so they sit outside every measurement. |
| **B205** | OPEN | **🔥 `nfl_game_results.wind` has been NULL on EVERY game since 2026-10-11 — the only validated edge in the system is dark.** 23 outdoor games, three straight weeks (10-11, 10-18, 10-25), zero wind recorded, while per-season fill ran 79–100% historically and 2026 was fine through 10-04. Cause: `backfill_nfl_result_weather.py` owns that column and **is referenced by no workflow** — run once by hand, never again. Fifth instance of the written-but-never-scheduled class that B157 built `audit_unscheduled_writers.py` to catch, so **check why the audit did not flag it** (it is reachability-aware and this script is imported by nothing). `nfl_weather_pull.py` IS scheduled but writes the ctx FORECAST, a different column. Consequences: (a) the wind lens cannot be GRADED going forward, (b) the forecast-vs-reading question that blocks B202 cannot be answered on real n — only 19 games carry both. Schedule it in `nfl_pipeline.yml` after the resolver, assert the OUTPUT moved rather than the exit code, and backfill 10-11 onward. The script is already safe by construction: never inserts, matches on (game_date, away_team, home_team) not game_id, fills only NULLs, weather columns only, reads back every PATCH. |
| **B206** | **DONE 10/26** (`grade_nba_props.py`) | **NBA props were never graded — now 9,783 are.** Andy: *"Why is NBA unmeasurable also?"* Because nothing wrote `result`. **My earlier claim that `nba_game_results` has no scores was WRONG** — 996 of 1,000 sampled rows carry a `home_score` back to 2024-10-28, and `nba_player_game_logs` had 969 player-games on 10-20..25. Both inputs existed; the grader B155 left open did not. **Shipped:** joins on (player_name, game_date) because `nba_pipeline_props.player_id` is NULL on EVERY row (checked, not assumed); maps pts/reb/ast/pra/threes/steals/blocks/turnovers with `fg3m` for threes since `threes_made` is mostly NULL; writes **Title-case** `result` because the other three sports use Win/Loss/Void/Push and WIN in caps would silently miss every downstream comparison. Three guards: a date with NO logs is **skipped, not Voided** (not-ingested is not DNP, and conflating them books DNPs as losses); a date WITH logs but no row for that player is a genuine DNP so Void (1,023 of them); a name matching 2+ log rows is left ungraded and counted (0 occurred). Batched by (result, final_value) via an encoded `id=in.(...)`: **9,783 rows in 237 requests, 0 failures**, not 9.8k PATCHes (B154). Remaining 1,395 ungraded are exactly tonight’s games. **Follow-up: populate `player_id` on the props writer** so the name join stops being a latent hazard. |
| **B207** | OPEN · **ACT NOW** | **🔥 NBA props have a severe systematic OVER-BIAS — first measurement ever, n=8,760.** Priced from `book_over_odds`/`book_under_odds`: **ALL NBA props ROI −10.50%**, and it is almost entirely one-sided — **overs 42.7% / −15.87%** vs unders 52.9% / −3.66%. EVERY over family is negative: `ast_over` 38.0% **−23.43%**, `pra_over` 43.0% −20.85%, `pts_over` 42.5% −18.10%, `reb_over` 42.3% −15.64%, `threes_over` 39.4% −11.68%. The only non-negative families are `pra_under` **+2.62%** and `pts_under` **+0.66%**. Same shape as `project_nhl_prop_over_bias_1009`, so this is a cross-sport pattern in how we generate props, not an NBA quirk — investigate the generator projection-vs-line asymmetry, not the NBA model. **Immediate action: stop publishing NBA overs** (ban at SOURCE and in the publishable VIEW — a prop-family ban needs all three layers per `feedback_prop_family_ban_three_layer`). Do NOT read the "vs BE" column on the raw output: mean American price is skewed when overs are plus-money and unders minus-money, so ROI is the trustworthy figure. |
| **B208** | OPEN | **NBA + NHL props: in-depth improvement plan (Andy asked for this list).** Both sports share three defects, in dependency order. **(1) NO TIERING AT ALL.** Every one of NBA 8,760 graded rows and 11,988 of NHL 12,034 in-band rows is tier **COVERAGE** with conviction 0-49. There is no gate, so nothing can be right or wrong — NHL sits at −6.80% ROI and NBA at −10.50%, which is roughly the undifferentiated market price. **Do not assign tiers off a projection first** — that is how NFL defaulted every edge-detected prop to STRONG (B135). Build a discriminator, measure it against each bucket OWN breakeven, then map tiers. **(2) THE OVER-BIAS IS THE FIRST THING TO FIX** (B207) and it is cross-sport, so fix the generator, not two models. **(3) NHL HAS ONE NUMERIC SIGNAL KEY** — no recent-form input at all, and unlike MLB there is no `_stat_last10`, so the lookback has to be BUILT (bounded by game_date from the start — see `project_prop_edge_was_the_l5_leak_1026` for what an unbounded window costs). **Sequencing:** B207 (ban overs) then build the NHL lookback, then measure whether recent form beats the line PER SPORT (on NFL it did not: standardised slope +0.061, too small for the vig), and only then tier. **Known-good reference:** MLB ML is the one surface that clears (+11.02%), and MLB prop conviction only *looked* monotonic because of the L5 leak — so do not copy the MLB prop recipe, it was never real. |
| **B209** | OPEN · **ACT NOW** | **🔥🔥 STOP PUBLISHING NFL CURATED SPREADS — 10-26, 27.8%, ROI −47.35%.** Measured 2026-10-26 on every graded NFL spread receipt (n=85, 09-13..10-25), priced from each receipt’s own `pick_odds`: **sharp_card 5-13 (27.8%, −47.31%)**, **sweat_card 5-13 (27.8%, −47.40%)**, game_read 21-28 (42.9%, −17.89%). Breakeven is 52.38%. Every NFL spread surface loses and the curated ones lose roughly half of stake. The engine’s own `audit_note` already says it: *"this is a measured loser, not variance... the tier was never earned."* **This is the one improvement available with no modelling** — removing a known loser raises the published record immediately. NFL MONEYLINE reads are deliberately untouched (+10.7%, and `project_nfl_curated_spread_is_the_leak_1018` says cap spreads, never ML). |
| **B210** | OPEN | **`pick_side` is NULL on 26 of 36 curated NFL spread receipts (72%) — the side we published is not stored on the row that gets graded.** All 26 sit on `sharp_card` (13) and `sweat_card` (13). Consequences: (a) any analysis that derives the covering side from `pick_side` silently DROPS them, which is exactly how I reported the NFL spread engine at 45.8% when the true all-receipt figure is **36.5%** — the 26 dropped rows went **4-22 (15.4%)**, so the omission biased the result optimistically by 9 points; (b) wrong-side detection (`pick_side == prop.direction`, and the badge/read/card/receipt invariant) cannot run on those rows at all. Same defect class as **B179** (`tier` empty on all 327 graded `game_read` rows): the discriminator is not stamped on the graded row. Fix at the receipt writer, then backfill from `pick_label` + `matchup` by SHAPE (per `feedback_parse_a_pick_by_shape_not_vocabulary`), and re-run every side comparison afterwards. |
| **B211** | OPEN · **ACT NOW** | **🔥 `nfl_injuries` IS A DEAD FEED — every status NULL, ~2 months stale, and it is the only availability gate props have.** All 40 PHI rows and all 40 DAL rows carry `status = None`, last updated **2026-08-31 / 09-01**. Consequence found live on the 10-26 MNF slate: **two conviction-84 STRONG props on Saquon Barkley, who has not played since week 4** (PHI played weeks 1-6, Barkley appeared 1-4, and weeks 2 and 4 were 4 and 2 carries — early exits). `nfl_rosters_current` lists him ACT as of 19:56Z, so the roster cannot settle it either: **his availability is unknowable from our data.** Two fixes, both needed: (a) repair or replace the injuries feed; (b) add an **absence gate** — refuse props on a player with a 2+ consecutive game absence, which needs no injury feed at all because the game logs already carry it. The gate is the durable fix; a feed can die again silently, and this one did. |
| **B212** | OPEN | **`nfl_rosters_current` is missing players who are actively playing.** DeVonta Smith (played wk1,2,3,6; carries two props tonight) and Darius Cooper (played all 6 weeks; carries two props) are **absent from the table entirely**, while it correctly carries Barkley, Lemon and McKee. So a roster-status check silently returns nothing for some players rather than failing — the same missing-row-reads-as-no-problem shape as the dead injuries feed. This matters because `nfl_rosters_current` is the source `project_qb_starter_chain_is_backward_looking_1023` named as the authoritative one to use INSTEAD of the 39-day-stale depth chart. An authoritative source with silent gaps is worse than a stale one, because nothing flags the gap. Audit coverage: count active players per team against the week-N box scores and alert on any team short of its skill-position starters. |
| **B213** | OPEN | **A published prop contradicted by the only data point we have.** `Tanner McKee pass_attempts_under 33.5` (id 47920, −115, tier **STRONG**, conviction **81**) on the 10-26 slate. McKee has exactly ONE start — week 6 — in which he threw **45 attempts**. The under needs a 12-attempt drop off the single observation, and the bounded line/L5 ratio is 0.74, which is the OVER-favouring end of the measured distribution. So our own feature points over and we published under at STRONG. This is not a one-off: measured 2026-10-26, NFL prop conviction is FLAT (corr with price −0.002, every bucket −2.3 to +1.0 vs its own breakeven) and tier **STRONG runs 51.4% against a 52.7% breakeven (ROI −2.94%) while LIGHT runs +3.37%** — so STRONG and conv-84 carry no information and should not be surfaced as if they do. Related: B203 (the stored projection is inverted), B199. |
| B5 | OPEN | **`expo-updates` / OTA.** Every client fix is days away; the App State fix missed the 1.0.4 cut by an hour. Highest-leverage infra item. |

## T2 — SOON

| ID | Status | Item |
|----|--------|------|
| **B136** | OPEN | **NBA player projections + predicted score from that data**, then roll the same structure to NFL. Needs minutes, blowout risk, pace, injury — Andy flagged late-game minutes as the hard part. Read B134(b) first: predicted score is the same problem in both sports. |
| **B137** | OPEN | **Clean up game-prop presentation in NFL game detail**, and **add game props to NHL + NBA game detail** (the props for that game). |
| B130 | OPEN | **MLB + NCAAB records still count every phase.** MLB hashed game_id joins to no feed; NCAAB has no fixed games-per-team to verify a fix against. Needs a phase feed joined on something other than a team name. |
| B122 | OPEN | **~65 `date.today()` call sites.** DST flips **2026-11-01** — ten days out. |
| B126 | OPEN | **NFL positional defense into the prop scorer.** The table is populated and the projection layer proved it carries signal (opponent factor −2.4 MAE, the only factor that helped). Needs shrinkage + grading before weight. Feeds B134(c). |
| **B124** | **DONE 10/24** | NBA's banner promised *"that fix is built and in review for the next app update"* — **1.0.4 was approved and released today, so that sentence was false to every NBA user who read it.** Cleared via `refresh_calibration_notes.py --clear-build-promise NBA`; verified `sport_registry.state_message` is now NULL and the next nightly writes the normal auto-generated note. Server-side data, so no build was needed — which is why it could be fixed the same hour the release landed. |
| **B145** | OPEN | **`jerry_reads.call_line` is still off-market after the 10-19 fix** — 10-22 Bears read carries CHI −1.5 against a close of 3.0. `project_jerry_published_fake_lines_1019` fixed the published side; the read's own line was not covered. |
| **B148** | OPEN | **The Fade has no monitoring, so "quiet" and "broken" are indistinguishable.** Checked 10-23: 0 plays at 16:13 UTC was *too early*, not an outage — fade receipts normally publish 19:49-20:27 UTC, because tonight's games have no public splits at midday. Two real smells found while checking: `generate_the_fade.latest_public_splits()` takes a `date` and **never filters on it**, so its "not on this slate: 2089" counts every key it ever saw; and `archive_public_splits.SUPPORTED_SPORTS` omits **NBA** (the per-sport `write_line_snapshot.py --sport NBA` call covers it, but the archive the pattern miner reads does not). Needs an alert on the surface with our only measured edge. |
| **B158** | OPEN | **Does the short-spread flip hold forward, and how much supply does the price gate cost?** The 81.8% is n=22. Two things to watch weekly: (a) the flipped record on its own receipts, and (b) how often a short spread is dropped for “no 2+ books post the flipped line” — that fired on 1 of 2 candidates on day one (NC State @ Stanford, board moved off +6.5), which is a conservative bias but real lost supply. If it is costing most of the band, consider pricing the flip at the CURRENT posted line instead of our stale one — a different bet, so it needs its own measurement. |
| **B159** | OPEN | **Band the long football spread before leaving it banned.** |line| > 7 is 49.0% n=253 as one bucket, but 7.5-14 and 14+ were never split, and `project_publish_layer_beats_projection_1010` found our projection 15.8pts light specifically on 14+ favourites. There may be a flip inside the long band that the single bucket averages away. |
| **B151** | **DONE 10/23** (`0fde5772`) | **The Fade was validated on SPREADS and shipped TOTALS — fixed by evidence, per (sport, market).** Re-measured the live feed: football spreads **+13.4% n=96** (the edge, reproduced), **MLB totals −11.7% n=281** and **NCAAF totals −3.4% n=170** — both now BANNED; NBA/NHL totals are **unmeasured (n=6, n=10)** and stay as labelled `tracking`, because banning on n=6 mirrors publishing on n=6. Pooled totals read −8.4% n=467 but MLB+NCAAF carry it and money flow inverts by sport. Every play now carries `evidence: validated|tracking`. 10-17 Saturday: 43 plays → 16, all validated NCAAF spreads. Premise was also partly calendar — fade receipts start 10-19 and that week had no Saturday slate. |
| **B160** | OPEN | **`nfl_game_results` and `nfl_game_context` share ZERO `game_id`s** — 272 each, 0 overlap (`project_nfl_game_id_mismatch_911`, still live). `grade_game_market_receipts` grades NFL off `nfl_game_results` keyed on `game_id`, while NFL picks are built from `nfl_game_context` (md5 hash ids) — results use `20261213_ATL_CLE` form. NFL receipts *do* currently show graded (32 game_read, 2 sharp_card), so some other path is filling them: **find out which, and confirm it covers the Sharp’s flipped NFL spreads**, or they ship unmeasurable. Also blocked the NFL arm of the B151 re-measurement (13 qualifying splits, 0 gradeable). No alias column exists on either table — the bridge has to be (game_date, home_team, away_team). |
| **B161** | **DONE 10/25** (`b7afbcfa`) | **The depth-chart scrape never wrote its output -- for 39 days.** Not a schedule problem: `json.dump` was the LAST statement in `main()`, after a diff step whose regex matched `_NFL_QB1_MAP`, a symbol renamed to `_NFL_QB1_MAP_FALLBACK` in 3fa6c92a -- the SAME commit that wired the scrape in. So it raised before writing on every run since the day it shipped, silently, behind `continue-on-error: true`. Save now runs BEFORE the diagnostics, asserts 24/32 QB1, and re-reads the file. Then the chart itself was wrong in 3 places (NYG Dart->McCarthy, PHI Hurts->McKee, ATL Rush->Penix; Dart and Hurts are both RES) and a hardcoded branch was DROPPING the scrape whenever it named Penix -- written for a Wk 2 injury that reversed, so it reinstated an INACTIVE QB over a correct feed on every rebuild. Removed. Audited all 32 against real box scores: 2 wrong, both playing that day (TB named Mayfield when Jalon Daniels started wk 4/5/6; BAL named Lamar Jackson, who was `Out`). Both now in `nfl_starter_overrides.json`, which `nfl_game_context` reads too -- one override mechanism instead of hardcoded branches. **Still open:** nothing persists the scrape across CI runs (no commit step, no table) -- carved out as B191. |
| **B162** | OPEN | **The weekend NFL slate has NO conviction: 0 of 13 picks are STRONG+** (5 LEAN, 6 COVERAGE, 1 PASS, 1 ML at conv 0 where the engine wrote "Engine passed"). So The Sharp gets **zero** NFL plays on 10-25 regardless of the B156 flip — the flip preserves the mechanism, but there is nothing to flip. Also **12 of 13 picks are spreads**, our documented leak market, while ML (our only +EV football market, +10.7%) was selected once and only as a pass. ML prices ARE available (`close_home_ml`/`close_away_ml` populated on all 13; only `home_ml_odds`/`away_ml_odds` are NULL, and the composer falls back correctly), so this is a **selection-weighting** problem, not a price gap. Belongs in B134. |
| **B163** | OPEN | **5 of 12 weekend NFL spread picks carry a line NO book posts** (verified against 12,000 `line_history` quotes): NYG +7 vs board +10/+10.5 (**3.5 pts worse than available**), SF −4.5 vs −3/−2.5/−2, LA −5.5 vs −4/−3.5/−3, CIN +3.5 vs a board topping out at +2, PIT −1.5 vs −3.5/−3. Mixed direction, so it is a stale/fabricated line rather than systematic shading. This is **B145 live on this weekend’s slate** and the same signature as `project_jerry_published_fake_lines_1019`. |
| **B164** | **DONE 10/23** (`54ee39d0`) | **Nothing alerts when a game kicks off with no read.** All 5 NCAAF games on 10-23 had no `jerry_cache` row and no watchdog fired; found only because Andy opened one. Add a check: any game with kickoff inside N hours and no read → CRITICAL. Same shape as B148 (quiet vs broken indistinguishable). | **Shipped:** `check_reads_missing_near_kickoff` in `watchdogs.py`. Measures `jerry_reads.long_read` (the table the app renders) against today’s ET slate, per-sport kickoff column, CRITICAL inside 6h, suppressed beyond 12h so it cannot become daily noise. Verified it both FIRES and suppresses, not just that it ran clean.
| **B165** | OPEN | **`team_stats_rolling` holds 274 NCAAF team names for ~136 teams.** The alias mirror added 10-23 makes lookups work, but the duplication remains and two writers still disagree on spelling. Consolidate to one canonical name at WRITE time, then drop the mirror. Also `sweat_rating` is **19.3 days** stale (139 rows) — separate writer, also needs scheduling. |
| **B166** | OPEN | **`jerry_cache` keys are inconsistent between sports, and it is NOT the table the app reads.** NFL rows store the WHOLE cache key in the `game_id` column (`game_read_<hash>_nfl_week_2026-10-22`) while NCAAF stores the bare id, so a lookup by `game_id` finds NFL reads for NCAAF but not for NFL. Low severity: `app/index.tsx` prefers `jerry_reads.long_read` and only falls back to the cache. **Correction 10-23:** I used `jerry_cache` to claim "NFL reads dark since 10-07" — that was WRONG. Measured on `jerry_reads`, NFL coverage was 14/14 (10-04), 13/13 (10-11), 11/12 (10-18), 12/12 (10-25). The only real NFL gap was 1 game on 10-18. Measure read coverage on `jerry_reads`, never on `jerry_cache` write timestamps. The NCAAF gap WAS real (0/5 and 14/44, measured on `jerry_reads`). |
| **B167** | OPEN | **Triage the 54 unscheduled writers** that `audit_unscheduled_writers.py` now reports. Not all should be scheduled — several are shadow/experimental (`ncaaf_shadow_sweat_tier`, `ncaaf_shadow_total_fade`, `shadow_panelist_engine`) and some are probably dead and should be DELETED rather than wired up. Highest-value in the list: `bridge_football_game_ids.py` (= B160, already written, never run), `price_picks.py` (= B168), `ncaaf_team_game_stats_pull.py` (the numerator for everything fixed 10/23), `compute_points_per_game.py`, `compute_margin_strength.py`, `populate_game_sor.py`, `grade_stranded_receipts.py` / `grade_orphaned_receipts.py` / `nba_grade_backlog.py` (records), `watchdog_market_sanity.py` + `ncaaf_daily_dq_audit.py` + `audit_dark_signals.py` (detectors). |
| **B168** | OPEN | **`price_picks.py` is reachable from nothing and pricing is degrading.** 100% of `jerry_reads` priced in early Oct → **55% on 10-23, 21% on 10-24 (73 reads), 0% on 10-27/10-28/10-31**. An unpriced pick cannot be graded for ROI or CLV, which is the same hole that left 145 of 160 reads ungradeable until 10-02. **Deliberately not scheduled yet** — it produced NO output on a manual run and needs debugging first; wiring a script I do not understand into the nightly is how dark steps get created. |
| **B169** | OPEN · **NEEDS A BUILD** | **The false-CLV fix is committed but NOT live** (`d62d2370`). Game detail claimed "we beat the close by 3.5" on NYG +7 against a +10.5 close, and hid real CLV wins on other dogs. Fixed in `app/components/GameDetailV2.tsx`, but there is **no OTA path** (`project_no_ota_path_client_fixes_need_builds`), so every dog pick can still show a false closing-line claim until the next build ships. 1.0.4 is in review — decide whether this rides 1.0.4 or forces a 1.0.5. Server-side CLV was never affected. |
| **B170** | OPEN | **Andy saw "Giants +7.5"; every stored copy says 7.0** — `primary_play.line`, `jerry_reads.call_line` and `public_receipts.pick_line` are all 7.0. If the card really rendered 7.5 there is a second defect (a rounding or half-point display step) that the CLV fix does NOT address. Needs Andy to confirm what the screen said before chasing it — do not assume a misread. |
| **B149** | OPEN | **Ladder has no sport after the World Series.** Rungs 52-63 are all MLB. Decide the post-MLB sport and price band before the streak (currently 4) dies of neglect. |
| **B150** | OPEN | **Ledger tier drift** — the Brewers ML leg is `STRONG` in `ledger_suggestions` and `PRIME` in `ledger_snapshots`, same price, same conviction 79. One of the two is wrong on every parlay. |
| **B189** | OPEN | **NFL externals are the thinnest football coverage we have, and they feed the one edge that works.** Andy: *"there should be more nfl externals we are dripping the ball there"*. Measured over the last 30d, unpaged (the 1000-row cap biases a single sample — query per sport): **NFL 8 sources / 880 rows** vs **MLB 13 / 912**, NCAAF 8 / 2,285, NHL 6 / 1,082, NBA 6 / 203, UFC 1 / 63. The five MLB-only sources are **betfirm, bettingpros, docsports, tonyspicks, vsin**. This is NOT broken-puller territory — all 8 NFL sources wrote today (166 rows: oddscrowd 53, scoresandodds 43, covers 16, sbr 14, action 12, dimers 12, pickswise 8, pickdawgz 8) — it is a breadth gap. **Why it matters more than it looks:** fading heavy public money on SPREADS is the one edge that holds in both halves (+12.4% n=107), and externals are what feed it. **Why breadth alone is worthless:** [[project_externals_are_mostly_money_flow_925]] found most "externals" are the same money-flow feed restated, and [[project_panel_is_one_voice_1014]] found 27 duplicate pairs where a "consensus of 4 of 5" was free because they were one number. So the work is **(a)** add the 5 MLB-only sources to the NFL pullers, then **(b)** measure pairwise side-agreement across all 13 and keep only the ones that are not a restatement of a source we already have. Correlated sources inflate apparent consensus and buy nothing. Also fix UFC (1 source) while in here. **Note the 9/11 memory note "NFL externals only 2 vs MLB 13" is now STALE — NFL is at 8.** VERIFY: per-sport `external_picks` distinct `source` where `pulled_at >= now()-30d`, paged past 1000. |
| **B190** | OPEN | **🔥 SIGNAL WEIGHTING + SCENARIO TUNING — how the engine picks once ALL signals are in.** Andy: *"regression and tuning how the engine picks when factoring all signals especially specific scenarios and general fine tuning of signal weighting"*. The ask is NOT another post-hoc layer on top of the scorer — it is the scorer's own combination step. Three parts. **(1) WEIGHTING.** Signal weights have never been fit; they are hand-set constants, and B173 (the real weight table) is still open because 931 of 2,034 `signal_attribution` rows had no recorded SIDE until the 10-24 backfill. That backfill is done, so B173 is unblocked and is the INPUT to this — fit weights against graded outcomes per sport and per market, do not hand-tune. Per-signal calibration is also still a known gap (09-29). **(2) REGRESSION.** No regression term exists at all; the engine cannot reason that a 67% cover rate or a 1-9 home ATS run reverts, which is the single factor that drove most of the 10-24/10-25 hand calls. It has to be MEASURED before it is weighted — the spread residual is unpredictable four ways ([[project_spread_residual_unpredictable_1018]]), 0 of 151/151/80 features cleared Bonferroni and ridge TEST R² was negative, so a regression feature must clear held-out significance or stay display-only. Same bar for reverse line movement. **(3) SCENARIO CONDITIONING — the one with the most evidence.** Every edge we hold is band- or scenario-conditional and the engine applies none of them that way: football spreads flip 66.0% n=103 at |line|≤7 but 49.0% n=253 beyond it; NCAAF tier inverts BY BAND (>10.5 STRONG+ 59.5% n=74, ≤10.5 STRONG+ 34.4%) and pooled they CANCEL to noise (B178); NFL STRONG+ short spreads run 32.0% n=25; money flow inverts by sport (MLB fade vs NCAAF follow); the fade works on SPREADS (+12.4% n=107) and is negative on totals. A single global weight per signal is provably wrong given that table — the weight has to be a function of (sport, market, band). **Start from B173, not from intuition.** Pairs with **B134** (NFL retrain, Mon 10-27) and B135 (recalibration cadence). VERIFY each candidate on held-out data with n and CI, and reject anything a CONSTANT matches ([[feedback_level_model_must_beat_a_constant]]). |
| **B191** | OPEN | **The depth-chart scrape still does not persist across CI runs.** Carved out of B161. `nfl_pipeline.yml` has no `git add`/`git commit` after `nfl_live_depth_scrape.py` (the comment claiming "File is committed post-scrape" was never true and is now corrected in place), and the script only writes a local JSON -- no DB write. So downstream steps in the SAME job see fresh data and nothing in a later run does. The durable fix is a table, not a committed file. Note `nfl_starters` is **100% box-score carryover** (every source is `box_score_*`, week 7 rows sourced from `box_score_w5`/`w6`, zero week 8 rows) so it is not a substitute -- it only knows the past. |
| **B192** | OPEN | **🔥 EVERY prop lookback field is 0% populated -- all 503 NFL props today.** Found while building the KC@SEA dive. `player_l5_hit_count`, `player_l10_hit_count`, `player_season_hit_pct`, `player_lookback_updated_at`, `consensus`, `consensus_delta` and `lineup_state` are **empty on every row**, while `signals` and `projection` are 100% filled. So the projection INPUTS are fed (signals carries `l4`, `_l4_wopr`, `_l4_racr`, `opp_pct`, `def_bot10`) but nothing can show or check a hit-rate history -- which is also why the prop graph has nothing to draw (**B182**) and why no credibility number can be surfaced next to a prop. Find the writer that fills these and whether it is scheduled (cf. B157, four unscheduled writers found in one day). Also: the matchup note on one prop read "Opp KC ranks #26 in pass defense" while `nfl_positional_defense` has KC 7th-MOST TE yards allowed -- same conclusion, opposite rank direction, so the note generator and the table disagree on which end is good. |
| **B193** | OPEN | **🔥🔥 PROP FILTER REDESIGN — measured on n=3,399, and the current one ranks BACKWARDS.** Andy: *"reevaluate how props are filtered and assessed ... better process and prediction and projection of stats i.e props against market"*. Attribution over every NFL prop resolved since 09-19. **BASELINE 1672-1727, 49.2% CI[47.5,50.9] vs a 54.2% breakeven — the CI's UPPER bound is below breakeven, so the book is unprofitable with confidence.** What is broken: **(a) TIER IS INVERTED AT THE TOP** — LIGHT 53.8% n=788 > LEAN 52.7% n=849 > STRONG 51.1% n=985 > **PRIME 46.4% n=168**; conviction deciles are non-monotonic noise. **(b) `anytime_td_over` is 28.5% on n=372** and is STRUCTURALLY the same population as the COVERAGE disaster and the negative-edge disaster — proven: after dropping anytime_td, dropping COVERAGE removes ZERO further rows, and so does an `edge_pct>5` gate. One ban fixes three symptoms and moves the book 49.2% -> 51.7%. **(c) projection MAGNITUDE carries no information** — edge_pct 05-10 50.1%, 10-20 52.2%, 20-35 49.5%, 35+ 51.6%, flat — but its **SIGN does**: negative edge is 30.5% n=151. So edge is a GATE, never a ranker. **(d) `opp_pct` is monotonic and never gated** — 0.0-0.2 53.8% n=706 down to 0.6-0.8 45.9% n=643. **WHAT TO SHIP:** publish **UNDERS gated on opp_pct <= 0.4 — 57.0% n=677, and it HOLDS BOTH HALVES (56.4% n=385 / 57.9% n=292)**. The same gate on OVERS is 51.2% n=547 with halves 52.0%/50.0%, so **do not publish overs**. Supply is ~19/day, ample for a card. Best families all UNDER: rush_yds 55.7%, rush_attempts 54.9%, receptions 54.8%, reception_yds 53.3%; worst are rush_yds_over 44.6% and pass_yds_under 44.1%. Pairs with **B138** and supersedes the ranking half of **B183**. |
| **B194** | OPEN | **⭐⭐ HOME FAVOURITES ARE OPPOSITE IN THE TWO FOOTBALL SPORTS, AND IT IS BAND-CONDITIONAL.** Measured straight from `*_game_results` for 2026, so no signal plumbing is involved, after validating each sport's `close_spread` sign (NFL 56/56, NCAAF 310/310). **NFL home favourites cover 38.6% (22-35-3, n=57, CI[27.1,51.6])** -- the CI upper bound sits just under the 52.38% breakeven, so fading them is directionally supported but **n is too small to stake**. **NCAAF home favourites cover 55.2% (238-193-1, n=431, CI[50.5,59.8])** -- the opposite direction -- and the **lay-11+ band clears outright at 58.3% (158-113, n=271, CI[52.4,64.0])**. Bands are non-monotonic: 0-3 56.2%, **3.5-7 40.0% n=60**, 7.5-10.5 55.8%, 11+ 58.3%. **Why this is the most useful number we have right now:** `COHORT_*_HOME_FAV` is 100% one-sided and `LR_SHADOW` is 90% collinear with it on NFL (B173), so the home-favourite view is what the engine is mostly expressing -- and it is RIGHT on NCAAF and WRONG on NFL. This is exactly the (sport, market, band) conditioning B190 calls for, with real n behind it. **Caveats before staking:** the NCAAF 11+ population is heavily FCS blowouts (Howard @ Indiana -58.5 class), so check market availability and limits before treating 271 games as bettable supply; and verify the NCAAF 3.5-7 hole is not a scheduling artefact. |
| **B195** | OPEN | **🔥🔥 "N SIGNALS AGREE" IS TRUE BY CONSTRUCTION — the ensemble breakdown is a JUSTIFICATION, not evidence.** Found while trying to build the cross-class collinearity gate for B190. `primary_play._ensemble_sources` contains chips for the CHOSEN side only: across **677 games (178 NFL + 499 NCAAF), ZERO contain chips on both sides of the same market**. Directional concentration is therefore 1.00 on every single game, so there is nothing to gate and, more importantly, **a "consensus" of 5 signals is not five confirmations — it is the five reasons the scorer kept after choosing**. Nothing records what argued against the pick. Same shape as [[project_panel_is_one_voice_1014]] ("a consensus of 4 of 5 is free when they are the same number"), one layer deeper. **Consequences, measured:** (a) **chip COUNT carries no information** — NCAAF 1-2 chips 55.2% n=134, 3-4 chips 49.3% n=71, 5+ chips 56.2% n=130, flat, so `signal_confluence_net` must not rank, tier or size anything; (b) the chosen side covers **54.3% n=335 CI[49.0,59.6] on NCAAF and 48.3% n=58 on NFL**; (c) NCAAF chips point HOME on **392 of 455** directional games (86%), independently confirming [[project_engine_fires_home_91pct_1010]]. **USER-FACING RISK:** if any surface tells a subscriber "5 signals support this play", that sentence is tautological and should be removed or re-expressed before the next build — we show the receipts, and this receipt proves nothing. **Also note** the per-class cap (`MAX_CLASS_SHARE=0.40`, fixed four times Aug 21-27) cannot see collinearity ACROSS classes: GB@DET on 10-25 had all five chips on HOME_RL from five DIFFERENT classes (model, team_form_season, discipline, team_form, roster_talent), every class far under cap. That gap is real but moot while the breakdown is one-sided by construction — fix the record first, then re-test the gate. |
| **B196** | OPEN | (hygiene) **`spread_result` has 4 bad values and 1 wrong label.** Found validating the new sign primitive against all 681 graded football games: `covers()` reproduces `spread_result` on **NFL 102/102** and **NCAAF 493/494**. The single mismatch is the STORED value, not the primitive — `2026-09-19 UAB @ Louisiana`, home laying 6.5 and winning by 7, is labelled `away_covered`. NCAAF `spread_result` also holds 4 junk values (`'true'` x3, `'false'` x1) where a cover label belongs. Low blast radius, but anything grading off that column inherits it. |

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

---

## 2026-10-27 — found in the Monday audit of 10-26

Every line below was verified by direct query; see `docs/daily/2026-10-27.md`
for the queries and the full numbers.

### B214 — OPEN — receipt sync wrote nothing for 10-26 on six surfaces
`sharp_card`, `sweat_card`, `potd`, `fade`, `ledger`, `daily_degen` all have
0 receipts for 2026-10-26; every day 10-16..10-25 has them. The cards WERE
built (`sharp_card_2026-10-26` 2 items @12:44, `sweat_card_2026-10-26`
@12:48). Build ran, sync wrote nothing, exit was green. **Highest priority —
yesterday is invisible in the published record.**

### B215 — OPEN — NBA reads unbridged and ungraded since 10-26
10-20..10-25 mapped 1:1; from 10-26 nine reads produced zero receipts and
`result`/`actual_outcome`/`resolved_at` are NULL on all nine. Likely the same
root as B214 or adjacent to it — check together, fix separately.

### B216 — OPEN — `anytime_td_over` has never graded a single row
430 receipts, 430 `NO_ACTION`, 0 W/L, all history. 13 of yesterday's 19 NFL
`NO_ACTION` props. Either wire the grader to a TD source or stop publishing
the family — with the inverted conviction from 10/22, prefer stopping.

### B217 — OPEN — NBA `rl` reads carry moneyline prices
14 of 27 priced outside a spread band (BOS −14.5 @ −750, HOU −4.5 @ **+167**).
Poisons every NBA ROI. Fix at the writer, then re-measure NBA from scratch.

### B218 — OPEN — NBA conviction ceiling is 65
0 of 106 reads ever ≥70 vs MLB 35.0% / NCAAF 31.0% / NFL 12.2%. Median 51,
33% Void. The scale is compressed, so NBA's 53.5% (23-20) is unselected.
Rebuild the NBA conviction scale before trusting any NBA read record.

### B219 — OPEN — `actual_value` regressed to zero on props
0 of 1,416 prop receipts in 14 days, vs 24.2% filled historically.
`game_read` is 0 of 1,360 ever. Receipts can't show their own work.

### B220 — OPEN — jerry regen overwrites the read a frozen receipt names
677 reads have `generated_at` after their receipt's `published_at`; that
explains 82% of the 198 result disagreements. Receipt is authoritative;
`jerry_reads` has lost its history. Version reads instead of overwriting.

### B221 — OPEN — 7 ML receipts void/no_action on a decided game
Sign-independent, so not a line-convention artefact. Includes 10-26 MNF
(`PHI ML -110` stored void; PHI lost 21-38).

### B222 — OPEN — curated surfaces run far under spec
`top_8` mean 1.4 over 14 days, four days at zero; `dawg` dark 14/14. The
Sharp mean ~5.3 against a 15-pick spec, and on 10-26 it was 2 items, both
MLB, both the same game — identical to the Sweat Card's 2, collapsing the
two surfaces into one.

### B223 — OPEN — POTD published a play the engine passed on
`reason = "⚠ Engine passed: MC sim has our side at 14% win prob"`, published
conviction 76, 2.0 units, as POTD. It won; the gate still failed.

### B224 — OPEN — `pick_line` holds two conventions in one column
174 rl receipts store a magnitude, 258 store a signed line, and 15 have a
label reading `-12.5` beside a stored `+12.5`. No single formula grades the
column. This broke my own audit grader before it broke anything else.

### B225 — OPEN — full NFL/NCAAF model reassessment (Andy, 10-27)
Something has to change in how football sides are selected. **Blocked on
data pulls first** — Andy's call: get the stats in before re-modelling, so
the reassessment is not run against the same thin inputs that produced the
current numbers. Depends on **B226 (G2)**: until an NFL receipt can join an
NFL result, every accuracy figure for the football engine is unverifiable.

Inputs to assemble before modelling:
  * team rolling stats with `refreshed_at` checked (NCAAF chain was stale,
    not wrong — 10/23)
  * opponent-adjusted rates, not raw per-game averages
  * the real close, not `open_spread` (it is a byte copy of `close_spread`)
  * preseason excluded via `sport_registry.season_start`, not the season col

### B226 — OPEN — G2: NFL receipts have no joinable game key
`public_receipts.game_id` is a 32-char hash (`4853610e…`), `matchup` is full
team names and NULL on 2 of 3 rows; `nfl_game_results` uses
`20261026_DAL_PHI` with `DAL`/`PHI`. Neither key matches, so **no NFL receipt
can be verified against a box score**. NFL grades currently come via
`jerry_reads.actual_outcome` — the same source that produced them, so there
is no independent check anywhere in the NFL chain.

### B227 — OPEN — targeted prop selection off matchup exploitation (Andy, 10-27)
Andy, verbatim shape of the ask: "maybe we don't assess every single prop,
maybe we assess fewer based on matchup that could lead to exploitation of
specific markets."

Worked example he gave: PHI at DAL. DAL has a poor run defence and
historically concedes yardage to elite RBs (above a rating threshold we
already reference). Prop Jerry should dig into *that game's* RB rushing
markets and assess whether Saquon over rush yards is a real play — rather
than pulling ~9,000 props a day, assessing none of them properly, and
feeding a conviction tier system that is broken as a result.

Why this is the right direction, from what is already measured:
  * 17,087 prop receipts, **7,970 void/no_action (47%)** — half the board
    was never gradeable in the first place
  * the only prop edge we ever found was the **L5 leak** (10/26)
  * prop projection beats the line **only where inputs were fed**
    (+0.103 fed vs −1.247 unfed) — volume without inputs is noise
  * `anytime_td_over`: 430 receipts, 430 NO_ACTION, never graded
  * conviction is already known non-monotonic, and inverted on anytime TD

Design shape to evaluate: a matchup gate FIRST (defence-vs-position
weakness, pace, role/usage), which selects a handful of games, and only then
a prop pull inside those games — so every published prop has a stated
reason, enough inputs to project, and a conviction that means something.

### B227 — MEASURED 2026-10-27 — matchup-gated props: plumbing yes, direction inverted

Tested Andy's pitch on **419 graded RB `rush_yds` props** joined to the
opponent's run defence from `nfl_game_context`, selecting the defence BY
SIDE (a home player faces the away defence — reversing that inverts
everything).

**1. The plumbing works.** 419 of 419 joined, **zero join failures**, zero
missing stats. `def_rush_ypg` / `def_rush_epa_allowed` are present for both
sides at pick time. Volume: 118 props over 17 dates = **6.9/day**, about
**2.6% of the 4,568 props we publish**. Exactly the "fewer and justified"
shape.

**2. The direction is inverted.** Rushing OVER gets monotonically worse the
weaker the run defence:

```
opp allows >=140 ypg   25.0% n=8    (thin)
opp allows >=130       36.0% n=25   (thin)
opp allows >=120       42.9% n=63   ROI -19.26%
opp allows >=median    45.8% n=118  ROI -13.78%
CONTROL   <median      47.0% n=115  ROI -11.29%
```

Every cell loses and the weak-defence cell is **worse than the control**.
It also fails split-half: **−24.27% early (n=72) vs +2.64% late (n=46)**.

**3. The mirror is the only positive cell — the opposite play:**

```
UNDER vs a STRONG run defence   57.0% n=93  ROI +7.66%  CI[47,67]
UNDER vs a weak defence         49.5% n=93  ROI -6.70%
```

CI does not clear BE 53.0%, so **not shippable** — but coherent with (2).

**4. The real find, which vindicates the instinct behind the pitch:**

```
corr(opp rush ypg allowed, projection - line) = +0.047   n=233
```

**Our projection does not use the run-defence matchup at all.** The input
exists, joins cleanly, and is unused. The matchup data *is* being left on
the table; only the sign was guessed wrong.

Also: **233 of 233 overs have projection > line**, because the over book is
selected on that — so `projection > line` carries zero information inside
it. Circular.

**"Elite RB" adds nothing:** line ≥32.5 & weak 49.3% vs & strong 47.9%.

**Next step:** build the gate as pitched, but feed the matchup into the
**projection** rather than publishing a direction off it, and shadow it.
Nothing in the grid clears breakeven yet.

## B228 — mlb_game_context.home/away_ops_last7 is never filled (4 dead signals)

Found 2026-10-28 by replaying every enabled game-path condition against live
context rows.

Four enabled MLB signals read it and can never fire:
`home_offense_hot_l7`, `away_offense_hot_l7`, `home_offense_cold_l7`,
`away_offense_cold_l7`.

The chain:
* `enrich_team_recency.py` computes L7/L14 team OPS and writes
  `mlb_team_offense.ops_last7` — correct in-season, and genuinely NULL right
  now because no team has played in the last 7 days (the regular season is
  over). Note the step still prints `✅ Upserted recency stats for 30 teams`
  with every value `-`, which is the green-step-writes-nothing shape.
* `backfill_team_ops_history.py` writes `mlb_game_results.home_ops_last7`,
  and that IS populated for every in-season game — 394/394 in 2026-04,
  426/426 in 2026-05, 417/417 in 2026-08.
* migration `20260524_game_context_ops_recency.sql` added the columns to
  `mlb_game_context` and its own comment says "game_context copies the
  relevant per-team values into the game context payload". **Nothing does
  that copy.** 0 of 21 ctx rows have ever held a value.

FIX: one copy step in the MLB context builder, sourcing from
`mlb_team_offense` (live) or `mlb_game_results` (historical). Deliberately
not built on 2026-10-28: there is no in-season data to copy until spring, so
the fix could not be verified today beyond "the column stops being NULL".
Verify next March by asserting the four signals fire, not by checking the
column is non-NULL.

---

# OPEN ISSUE REGISTER — opened 2026-10-28

Andy: *"Ensure that anything that you identify as an issue is recorded and
has plan to move forward with a fix."*

Every issue found on 2026-10-28 that is **not already fixed**. Each carries
the measurement that found it and the next concrete action. Fixed items are
in that day's commits and the daily log, not here.

Ordered by expected cost of leaving it alone.

---

## R1 — The same surface publishes two different records

`compute_surface_records.py:516` skips any pick with `conviction == 0` or
`tier == 'COVERAGE'`. `public_receipts` counts them. On graded `game_read`:

```
receipts ledger  (all picks)        933-716 = 56.6%
surface_records  (coverage removed) 750-593 = 55.8%
the excluded group itself           308 picks, 59.8%, +20.09u
```

The excluded picks are the **better** half, and the exclusion is wildly
sport-skewed — share of graded `game_read` that is COVERAGE/conv-0:

```
NBA 84%   NHL 62%   NFL 43%   NCAAF 9%   MLB 3%   UFC 0%
```

So `surface_records` drops 84% of NBA's game reads and 3% of MLB's, which
makes any cross-sport comparison of that surface meaningless.

**Why it is not already fixed:** which denominator is the real one is a
product decision, not a defect. Counting COVERAGE raises our published
volume and includes picks the engine labelled low-confidence; excluding it
hides 62-84% of two sports' output.

**Plan:** Andy picks one rule and it is applied in BOTH places.
Recommendation: count everything that reached a user (the receipts rule),
and show tier alongside, because a pick that was published is part of the
record whatever label it carried. Then delete the skip in
`compute_surface_records` and re-snapshot. Needs
`snapshot_surface_records.py` run first so the before/after is diffable.

## R2 — MAX_CLASS_SHARE: one infeasible constraint and one real algebra bug

### MEASURED AND SPLIT IN TWO — 2026-10-28

The 0.40 cap does not hold on 464 of 642 stored `primary_play` breakdowns
across five sports. Splitting them by how many classes actually CONTRIBUTE
(contribution > 0) separates two different problems that had been read as
one, and the first theory — negative class totals — was **wrong**: zero
picks carry a negative class.

```
                                            picks   violating   feasible?
fewer than 3 contributing classes            391       391         NO
three or more contributing classes           251        73         yes
```

**391 of 464 are arithmetically impossible.** A 0.40 cap needs at least
ceil(1/0.40) = 3 classes to be satisfiable; with 2 the floor is 0.50 and
with 1 it is 1.00. 261 picks have exactly ONE contributing class — one class
is the entire case for the pick, and no arithmetic changes that. The 0.40
figure quoted on those picks was never meaningful. They do skew low-tier
already (255 COVERAGE, 85 LEAN, 8 STRONG), so thin evidence does track tier.

**73 are the real bug**, at 0.436-0.528. Cause: the loop walks a list sorted
once, descending, and capping a later class shrinks `working_total` again —
which pushes an already-capped class back over. The 2026-08-26 note
anticipated the multi-class case but a single descending pass is not a fixed
point. 0.44-0.53 is exactly one extra round of shrinkage past 0.40.

### SHIPPED: a shadow, not a fix

`ensemble_scorer.py` now computes `cap_audit` on the winning candidate and
carries it on `MarketDecision`: `n_contributing`, the feasible cap for that
pick, `share_live`, `share_fixed_point`, and `live_cap_violated`. Verified
on 243 live decisions, 0 errors — 74% infeasible, 15 genuine violations
where `share_live` 0.411-0.489 collapses to 0.400-0.403 under a fixed point.

It is a shadow **by design**. Enforcing the cap correctly lowers the score
on hundreds of live picks and would re-tier them the same day with no
measurement of whether the re-tiering helps — and on this same board `tier`
is measuring anti-predictive on `game_read` in four sports (R18). Shipping
an unmeasured scoring change into that is how SP+ K=0.85 and
`classes_boost` happened.

**Plan:** the assertion the old plan asked for now exists and runs on every
pick. (1) Let `cap_audit` accumulate ~2 weeks. (2) Then decide the cap rule
with evidence: either `max(0.40, 1/n_contributing)` as a satisfiable cap, or
drop the cap and gate on `n_contributing` directly, which is the thing the
cap was a proxy for all along. (3) A pick whose entire case is one signal
class is the `project_panel_is_one_voice_1014` problem restated — worth
deciding whether it should be card-eligible at all, which is a selection
decision for Andy, not a cap repair.

## R3 — Population 1 RESOLVED by evidence; population 2 has a source

### The market-change population: keep the receipt. Measured 2026-10-28

First, a count correction. The register said 63. Measuring every receipt
citing `jerry_reads` rather than only the reconciler's window:

```
receipts citing jerry_reads                  2,147
the read now names a DIFFERENT market          211
of those, graded                               165
```

So the population is larger than recorded. The split by market pair shows
this is not one sport's quirk:

```
NHL    receipt=ml     read now=rl      37
NCAAF  receipt=total  read now=rl      20
NCAAF  receipt=ml     read now=rl      20
MLB    receipt=total  read now=ml      12
NFL    receipt=ml     read now=rl      11
NBA    receipt=ml     read now=rl      11
```

**As published, those 165 are 92-72 (56.1%), -2.94u** — above breakeven on
hit rate, slightly negative on units once juice is paid.

**The decision the plan asked for is now answered, and it is not a
judgement call.** "Replace the receipt with the market the read settled on"
is not an available option:

```
2026-09-22  MLB  Over 7.0              total   ->  Pass   pass   LOSS
2026-09-22  MLB  San Diego Padres ML   ml      ->  Pass   pass   LOSS
2026-09-22  MLB  Under 7.5             total   ->  Pass   pass   WIN
```

Several reads now say **Pass**. A Pass cannot be graded, and replacing an ML
receipt with a total would record a bet we never showed anyone. The receipt
is what reached the user; `jerry_reads` is a mutable row with 41 writers and
no row-level write protection (B41), rewritten after the fact. Same
principle already settled for the sign: the published artefact is
authoritative, not the source row.

**Resolution: keep the receipt's market, document why, no repair.** The
reconciler already reports rather than repairs these, so the code is correct
as it stands.

### The 34 MLB receipts: the start-time source exists and is not the Stats API

The plan proposed the MLB Stats API schedule or a new `first_pitch_utc`
column. Neither is needed for the mechanism — **`line_history.commence_time`
is already stored on every quote**, for every sport:

```
line_history.commence_time = 2026-10-29T00:10:00+00:00
mlb_game_context           no time column at all
mlb_game_results           no time column at all
```

**Honest limit, stated rather than buried:** `line_history` holds nothing
before 2026-10-14, so `commence_time` fixes this FORWARD and will not
recover most of the historical 34. For those, the MLB Stats API schedule
remains the only route, and it is worth asking whether 34 historical
receipts justify it.

**Plan:** wire the pre-game check to `line_history.commence_time` (forward,
all sports, no migration). Decide separately whether the historical 34 are
worth a Stats API backfill.

### The 25 after-kickoff receipts

No action, unchanged. The receipt is the honest record of what we published.

## R4 — Receipt pricing: 563 recovered, and the plan's premise was wrong

### PARTIALLY CLOSED 2026-10-28

The original plan here was to price history from `line_history`. **That
premise was false and measuring it first is what caught it:** `line_history`
holds nothing before **2026-10-14** in any sport (verified min `captured_at`
per sport), while unpriced receipts run back to April:

```
2026-04  39    2026-07 240    2026-10  78
2026-05 167    2026-08 543
2026-06 250    2026-09 632
```

Of 1,949 unpriced GRADED receipts, `line_history` can reach **23**. An
earlier probe reported "0 of 40 found in line_history" — that was a
keyspace mismatch, not an empty table (MLB keys on an odds-API hash, NHL on
NHL ids, NBA on ESPN ids), and treating a zero read as a finding is the
`feedback_zero_rows_is_a_wiring_error` trap.

**The source that does go back is the per-sport results table**, which
stores closing moneylines for every sport and puckline/total prices for NHL.

### THE ACTUAL DEFECT

`backfill_receipt_prices.py` already did this lookup, and had since 10-04 —
but only when the RECEIPT's own `source_table` was already
`mlb_game_results`. Every receipt that needs it comes from `jerry_reads` or
`jerry_cache`, so the branch never fired once: 280 graded MLB moneyline
receipts sat unpriced while the closing price for all 280 games was stored
one join away, 268 of them non-NULL.

That is the **seventh** instance this week of one correct rule placed behind
a single path instead of at the point of use.

### SHIPPED

* `_close_price()` — a final fallback dispatched on what exists FOR THE
  GAME, not on where the receipt came from. All five sports' closing ML,
  plus NHL puckline and total prices. `basis='close'` is stamped on every
  row, because a closing price is real and pre-game but is NOT the price we
  published at (`project_reconstructed_prices_inflate_roi_1009`).
* It refuses to invent a price. MLB/NFL/NCAAF/NBA store no spread-price or
  total-price column anywhere, so those stay NULL rather than being assigned
  a conventional -110 — an invented -110 is indistinguishable from a posted
  one.
* **The LABEL decides the side, not `pick_side`.** Grading resolves from
  `pick_label`, and this script's own header measured 18.6% of receipts
  disagreeing with their source row. Pricing by `pick_side` would attach the
  other team's price to a receipt graded on the label's team. In the event
  the override fired **0 times** — so no value changed, but the code is now
  correct by construction rather than by luck.
* `--only prices|matchup|tier|all`, so a price fix cannot silently also
  write 1,049 `tier` values read out of the mutable prop tables. Tier feeds
  the records whose denominator is R1's open question, so it is a records
  change needing its own decision.

**Result: 596 prices written** across two passes (567 before the football
alias was wired, 29 after), every one verified by read-back, 0 failed.

Measured effect on the thing that actually matters — unpriced GRADED
receipts, before today against now:

```
sport  market   before    now   priced
MLB    ml          318     18      300
MLB    prop        384    177      207
NCAAF  ml           31      3       28
NFL    ml           24      7       17
NHL    ml           46     42        4
TOTAL             1,949  1,431      518
```

A few cells read slightly higher now (MLB total 449 -> 464) because games
graded during the day added new unpriced rows; that is new volume, not a
regression.

**Still open, with reasons rather than a promise:**
* **403 prop receipts** — props have no price column in any results table.
* **142 POTD + 70 sweat_card POTD** — `daily_best_bet_history.odds_american`
  is NULL for those dates and there is no second source. Forward capture is
  fixed; this history is likely unrecoverable and should be stated as such
  rather than carried as a to-do.
* **~496 spread/total rows** that only a per-market price column could
  price. The real fix is forward: store the spread/total price at publish.
* **59 NFL receipts join 0 of 59** — see R19.

## R5 — CLOSED 2026-10-28. No NHL prop cell clears its own price. Keep the gate shut

`nhl_pipeline_props` holds 40,983 graded rows at tier COVERAGE and nothing
above it, so NHL has published **0 prop receipts, ever**. The question was
whether any cell deserves a gate. Measured: **no.**

### The first cut was wrong and would have shipped a fake edge

Scored against a flat 52.83% prop breakeven, four cells look like strong
edges:

```
goals_under    n=3,823   84.1%   "CLEARS"
assists_under  n=2,534   70.2%   "CLEARS"
points_under   n=4,015   67.5%   "CLEARS"
sog_under      n=5,349   54.9%   "CLEARS"
```

Every one of those is an artefact of `feedback_flat_110_inflates_every_ml_figure`
applied to props. A `goals_under` hitting 84% is not an edge when the book
charges **-900** for it. Re-scored at each row's OWN book price:

```
prop_type        n      hit%   med price   med BE%     ROI
goals_under    3,823   84.1%      -900      90.0%    -4.03%
assists_under  2,534   70.2%      -290      74.4%    -3.19%
points_under   4,015   67.5%      -227      69.4%    -3.96%
sog_under      5,349   54.9%      -139      58.2%    -3.28%
```

The hit rate is high because the price is high. Nothing clears.

### Within the publishable -300..+150 band, the same answer

```
direction      n      hit%   med price   med BE%      ROI
under        7,603   58.7%      -156      60.9%    -2.61%
over         5,955   46.2%      -105      51.2%   -12.46%

pooled      13,558   53.2%                          -6.93%
```

### Both places a selection edge could have hidden are closed

**Conviction** does not rescue it. The 70-79 band's apparent 67.6% is
entirely price: its median breakeven is 67.2%, ROI **-9.36%**, and inside
the band it is **-20.12% on n=61**. The `>=80` band is n=13.

**saves_under** was the single non-negative cell (+3.82%, n=279) and it
**fails split-half**: +12.84% then **-5.13%**.

### Conclusion

Every prop_type, every direction, every conviction band is negative at its
own price. **NHL publishing zero props is correct behaviour, not a bug.**
Opening the tier gate would ship a board measured at -6.93% ROI.

**Plan:** do not touch the gate. The work NHL props need is input quality
(`project_nhl_prop_inputs_lose_to_price_1003`), not gating — and that is a
modelling project, not a register item. Re-open only if a projection change
moves a cell's ROI positive AND it holds split-half.

## R6 — NCAAF conviction is inverted above 60 and it holds both halves

Published reads, by conviction band:

```
50-59  n=166  53.0%   +1.01u
60-69  n= 92  48.9%  -10.64u    49% / 48% across halves
70+    n=187  50.3%  -14.75u    52% / 48% across halves
```

NCAAF is the highest-volume sport. Its top two bands lose 25.39u combined
and the pattern survives a split. A candidate mechanism is already recorded:
`LR_SHADOW`, its highest-influence signal, is a coin flip on its own side
(51.4% on n=422) and was carrying weight 0.394 until today's refit zeroed
it.

**Plan:** re-measure the bands after two weeks on the corrected signal
weights (LR_SHADOW 0.394→0, COHORT_HEAVY_HOME_FAV 0.780→0,
COHORT_HEAVY_HOME_DOG 0.000→0.405). If the inversion persists on fresh
picks, conviction needs a per-sport refit — `base = 50 + min(win_score*10,
30)` is MLB-calibrated and `win_score` is already documented as
non-monotonic. Do not gate on conviction for NCAAF in the meantime.

## R7 — No NFL prop cell clears breakeven, and conviction is inverted there too

Published NFL `prop_jerry`, all time, against a 52.4% breakeven:

```
BY TIER        LEAN   n=1,116  52.2%  -18.97u    STRONG n=1,037  50.7%  -40.32u
BY CONVICTION  50-59  -11.46u   60-69  -22.16u   70-79  -23.45u   80-89  -37.96u
BY DIRECTION   OVER   n=1,510  50.1%  -76.61u
WORST FAMILY   rush_yds_over  n=158  44.3% [36.8, 52.1]  -25.74u   BELOW
```

Every cell straddles except `rush_yds_over`, which is below. Higher
conviction loses more, monotonically.

**Plan:** this is a volume decision, not a model fix — 250+ picks a slate
from a population with no measurable edge. Options to price out for Andy:
(a) publish only cells that clear their own CI, which today is none;
(b) cut volume hard and keep the surface alive for coverage;
(c) stop publishing NFL props and keep generating them for measurement.
Needs Andy's call on what the surface is FOR before any code changes.

## R8 — CLOSED 2026-10-28. The signal was fine; the LOADER was wrong

**Resolution:** `nhl_legacy_conviction_strong` is correctly configured
(`class=prop_form`, `subject_scope=prop`). `ensemble_scorer._load_sources`
filtered on sport only, with no `subject_scope` exclusion to match
`prop_ensemble_scorer._load_prop_sources`, so all **136** enabled
prop-scoped rows were evaluated against every game (MLB 71, NFL 22, NBA 19,
NHL 14, NCAAF 10).

No contamination — all 136 reference `p.*`, which does not exist in the game
namespace, so every one raised and returned False. Published picks verified
identical before and after the fix. The costs were 136 wasted evaluations
per game and 136 `!!` log lines per run that would have buried real
findings. NameError lines per NHL run: **136 → 0**.

Original entry kept below for the trail.

### original entry

Caught by the eval logger shipped today, on its first real run:

```
!! signal_expr[nhl_legacy_conviction_strong] NameError: name 'p' is not
   defined  <- this can never be true on ANY row
   expr: p.get("conviction") is not None and float(p["conviction"]) >= 70
```

`p` is the prop binding; the game evaluator has no `p`. Dead since written.

**Plan:** decide whether it belongs to the prop scorer (then set
`subject_scope='prop'` so the right evaluator sees it) or should read
`ctx` (then rewrite the expression). Check the same pattern across all
sports — the condition audit (`audit_signal_conditions_all_sports.py`) now
reports RAISE separately, so re-run it after any change.

## R9 — MEASURED 2026-10-28. The family does NOT transfer as a family

Enabled signal classes per sport: MLB 22, NCAAF 20, NFL 20, **NHL 11,
NBA 11**. NHL carries evidence on 90% of its registry, so it is variety-poor
rather than calibration-poor. `cohort` is the largest family it lacks (MLB
15 rows, NHL 0, NBA 0).

### The plumbing transfers. The RULES mostly do not.

The cohort family reads 10 ctx fields. The ones it leans on are present and
fully populated on both sports, and the ones that are missing are precisely
the baseball-specific ones (bullpen ERA, NRFI tier, `spread_delta`):

```
signal_confluence_net   NHL 297/297    NBA 155/155
home_ml_close           NHL 212/297    NBA  76/155
close_spread            present        present
```

So each is an insert, not new logic — which is exactly why the rule had to
be measured first. `backtest_cohort_transfer_nhl_nba.py` runs the five
evaluable rules verbatim from the live MLB rows, priced at each game's own
closing ML, with two direction-only controls:

```
NHL (n=197 joined, graded, priced)
rule                          n    hit%   med BE      ROI    split-half
confluence_home_lean_as_fav  43   74.4%   66.0%   +11.79%   +7.6 -> +15.7
confluence_home_lean         60   68.3%   64.2%    +7.06%  +10.7 ->  +3.4
sharp_confluence_alignment   46   63.0%   65.7%    -4.69%
confluence_away_lean         60   55.0%   55.1%    -5.27%
CONTROL always HOME         197   56.3%   54.5%    +1.16%
CONTROL always AWAY         197   43.7%   49.5%   -13.98%
```

**NOT SHIPPED, and the reasons are specific:**

* The two positive rules **do** beat the direction-only control (+11.79% and
  +7.06% against +1.16%) and hold their sign split-half. That is the
  encouraging half and why they are worth carrying.
* But **neither CI clears its own breakeven.**
  `confluence_home_lean_as_fav` is [59.8, 85.1] against a 66.0% breakeven —
  lower bound below the price, on n=43.
* **Two of the five are negative on NHL.** `confluence_away_lean` -5.27% and
  `sharp_confluence_alignment` -4.69%. Enabling all five because two look
  good is the precise mistake behind `project_sp_plus_compression_927` and
  the retracted NFL prop edge.
* **NBA is sample-blocked, not signal-blocked.** Only 63 games have a
  confluence value, a closing ML and a final score together; every rule and
  both controls are negative. There is nothing to conclude yet.

**Plan:** re-run the script as NHL volume grows. Bar for enabling is the
same one today's b2b and oddscrowd signals were held to — a CI that clears
breakeven AND split-half holding. `scenario`, `split` and `defense` are
untouched and should get the same treatment one at a time, never as a batch.
`external_pick` was the fifth class and did clear, which is why it shipped
today and these did not.

## R10 — CLOSED 2026-10-28. It was preseason, plus four Clippers games

The "~30% of NBA games have no line" framing was wrong, and the earlier
count of 13 of 68 was measured on a moving denominator. Correct measurement:

```
nba_game_context rows                        155
missing close_spread                          84
  before the 10-21 opener (PRESEASON)         60   <- not a defect
  after today (NOT YET PLAYED)                19   <- nothing has closed
  regular season, played                       5   <- the real gap
```

**60 of 84 are preseason.** Books barely line those games and nothing
downstream should read them — see
`project_situational_records_include_preseason_playoffs_1022` for what
counting preseason has already cost. Another 19 are future fixtures; a game
that has not been played has no closing line by definition. The first run of
the fix script counted those 19 as targets, which is why the window is now
bounded at both ends.

**4 of the remaining 5 were Clippers games** — the same alias bug as this
morning's phantom rows, with every quote orphaned under a minted id (R12).
The 5th (Chicago Bulls @ Milwaukee Bucks, 10-23) had quotes under its own
ESPN id and simply never had ctx filled.

**Fixed:** `fill_nba_ctx_close_from_line_history.py`. Joins on `matchup` +
capture batch rather than `game_id`, so orphaned rows are reachable without
rewriting history. **Patched 5, failed 0**; the only ctx rows still without
a close line are the 19 unplayed ones.

Two things worth keeping from the build:
* It takes the **modal posted line** and the median price only among books
  posting THAT line. A plain median across books can invent a line no book
  posted (`feedback_consensus_line_must_be_posted`).
* An unfiltered `line_history` read is cancelled server-side (57014). It
  queries per matchup. A timeout returning partial rows would have priced a
  game off a handful of books.

**Cross-check that this is reading the right thing:** the recovered totals
for 10-21 / 10-23 / 10-25 are 226.5 / 225.5 / 223.0 — matching, exactly, the
values independently recovered from the deleted phantom rows this morning.

## R11 — The MC dissent tier demotion is unproven, on n=26

Today's change stopped the gate zeroing conviction. The TIER demotion was
deliberately kept: n=26 is thin and a demotion lowers exposure. But the
evidence that exists points the other way — dissented picks went 61.5%
against a 52.6% control, and MC is 11-12 points too pessimistic in the band
that triggers it.

**Plan:** re-measure at n≥60 dissents. If the dissent group is still at or
above the control, remove the tier demotion too. Until then the gate is
recording its own evidence, which is why `_mc_dissent` stays on the blob.
Separately: MC's calibration is the deeper problem and worth its own study —
its most confident band (72.5% predicted) delivers 57.9%.

## R12 — The alias fix is VERIFIED LIVE; orphans are now consumed, not repointed

### Status 2026-10-28: live defect confirmed gone, historical rows handled

The alias fix (commit 5717af94) is not just present in the file — the
switchover is visible in production data for tonight's Lakers @ Clippers:

```
minted id   first 2026-10-27T23:26:19   last 2026-10-28T13:26:21
ESPN id     first 2026-10-28T16:26:22   last 2026-10-28T21:26:23
```

Two consecutive polls, writes moved. Nothing new is being orphaned.

**The orphans are no longer repointed, and that is deliberate.** Rewriting
`game_id` on historical quotes edits the audit trail of what we saw and
when, in order to fix a derived table. R10's fix instead READS them via
`matchup` + capture batch and writes only the derived `close_*` columns.

**One thing the build turned up that was not in the original entry:**
`line_history.matchup` stores the RAW Odds API string, so it is un-aliased
even on rows written AFTER the alias fix — the alias is applied when
deriving the `game_id`, not when storing the matchup. An exact-match lookup
therefore reported 4 of 5 games as having "no line_history under ANY id"
while 400 quotes sat there. Any future consumer of `matchup` needs the same
alias expansion; it is in `MATCHUP_ALIASES` in the fix script.

**Checked for other sports, and NCAAF is a false positive.** NCAAF
"minted-looking" ids (`ncaaf_20261031_Washington_Nebraska`) are its NATIVE
keyspace — NCAAF `game_id` is built from team names by design
(`project_ncaaf_five_team_name_mismatch_1021`). NHL, MLB and NFL show zero
minted ids. NBA had exactly the one. Note the probe was capped at
PostgREST's 1000 rows, so that is a floor, not a census.

**Plan:** no further action unless an analysis needs historical Clippers
line MOVEMENT (the derived close lines are now filled). If one does, repoint
in a single audited pass rather than ad hoc.

## R13 — `prop_playbook_decisions` denominators before 2026-10-28 are a random subset

The prop scorer read `limit or 500` unordered, so NHL and NBA slates were
scored at 19-33% coverage and a different arbitrary subset each day. Fixed
today, but every measurement taken on that table before 10-28 is over a
biased denominator.

**Plan:** do not quote pre-10-28 playbook rates. Re-measure after ~10
full-slate days (around 2026-11-07) and treat that as the first honest
baseline.

## R14 — Nine oddscrowd fade signals are held at weight 0 pending evidence

Repaired from dead but too thin to measure (MLB 3, NCAAB 3, NFL 3). Held at
0.0 rather than the 0.3 prior because every measurable sibling scored
0.00-0.15.

**Plan:** the weekly refit raises them automatically once they have ≥10
graded firings. No action needed; listed so the zeros are not mistaken for a
ban.

## R15 — `external_picks` only goes back to 2026-07, so "lifetime" is ~90 days

Not a bug, a data limit, and the cause of Andy's "records reset for
postseason?" question. The 90-day and 9999-day windows hold identical
numbers because that is all the history the scrapers stored.

**Plan:** state it on any surface that shows a lifetime external record.

### The app-side NULL risk is VERIFIED SAFE — 2026-10-28

Today's n>=30 floor makes `hit_rate` and `roi` NULL where they used to be a
number, so the worry was an app rendering NULL as `0%` — a withheld rate
reading as a catastrophic one. Audited every consumer rather than assuming:

`external_source_track_record` is read in exactly **two** places, and
`roi` is **never selected** by either.

```
GameDetailV2.tsx:424   selects source,surface,n_wins,n_losses,hit_rate
index.tsx:10442        selects ...,hit_rate,n_picks,n_wins,n_losses,n_pushes
                       -> normalized into LineMovementTab
```

Both degrade correctly:

* **HandicappersRow** (GameDetailV2:3578) renders `{w}-{l}` from
  `n_wins`/`n_losses`, which are **not** withheld, gated on `(w+l) >= 5`.
  `hit_rate` is used only to pick hot/cold colour and is guarded by
  `rec?.hit_rate != null`. A NULL rate therefore costs the chip its colour,
  not its record.
* **LineMovementTab:893** gates on `row.hitRate != null && row.n != null &&
  row.n > 0` before `.toFixed(1)`, and its type is already
  `hit_rate: number|null`.

**No app change required.** Worth noting the reason it was already safe:
both sites were written to handle a withheld thin-sample rate (the n>=5 gate
and the 2026-09-26 "say n=2 so the absence is a fact rather than a gap"
note), so the n>=30 floor landed on code that already expected NULL.

## R16 — Degen leg-count cap is confounded and unresolved

3-leg slips are +21.99u and 4-leg are -14.39u, but the two barely overlap in
time (82 of 109 four-leggers predate 2026-07-19; 57 of 58 three-leggers
postdate it) and 4-leg's own second half is +4.54u on n=27.

**Plan:** no action. The generator already caps at 3 and that is holding
(14 of 14 slips since 10-07). Revisit only if the cap is ever loosened.

## R17 — CLOSED 2026-10-28. Both scripts now have `--dry-run`

`compute_external_source_records.py` was the last write-only one.
`run(sport_filter, dry_run)` now threads the flag, prints the rows it would
write grouped by (sport, window) plus the count whose rate is WITHHELD for
`n_graded < 30`, and returns before the first write.

Verified: `--sport NHL --dry-run` reports 21 rows per window, all 21 with
the rate withheld, and writes nothing. Commit 2f48a129.

This mattered because a missing `--dry-run` overwrote a live degen card
earlier the same day.

## R18 — TIER IS ANTI-PREDICTIVE ON game_read, IN FOUR SPORTS

Found 2026-10-28 while pricing the MC tier demotion. This is the largest
open finding on the board and it reframes the Sharp's non-MLB problem.

Graded `game_read` receipts, pooled:

```
tier        n      hit%    95% CI          units
PRIME       24    58.3%  [38.8, 75.5]     -0.50u
STRONG     208    51.4%  [44.7, 58.1]    -13.52u
LEAN       200    49.0%  [42.2, 55.9]    -16.95u
COVERAGE   277    61.4%  [55.5, 66.9]    +22.71u
```

**The lowest tier is the only profitable one.** STRONG and LEAN — the two
tiers the curated surfaces select from — both lose, and together they cost
30.47 units.

And it is not one sport's mix. COVERAGE is top in every sport with enough
volume to judge:

```
sport   STRONG                LEAN                  COVERAGE
NCAAF   47.6% n=126 -16.69u   50.0% n=150 -8.61u    63.6% n= 44 +4.34u
NHL     56.7% n= 60  +0.67u   39.1% n= 23 -7.45u    61.8% n=144 +13.35u
NFL     --                    50.0% n= 24 -1.24u    57.1% n= 42 +1.75u
NBA     --                    --                    61.7% n= 47 +3.27u
```

Four independent sport populations, same direction.

### Why this explains the Sharp

The Sharp selects high-tier picks. NHL `rl` on the Sharp is 7-13 (35.0%,
-4.93u) and **all twenty were tagged STRONG**. The surface is drawing from
the two buckets that lose.

It also explains R1 from the other side: `compute_surface_records` excludes
COVERAGE, and the excluded group is the better half (+20.09u). We are
excluding our best picks from the record and selecting our worst onto cards.

### The limitation, stated plainly

**No split-half is possible on this field.** `tier` was hardcoded `None` on
game_read receipts until B179 (all 327 graded spread receipts carried no
tier), so every tier-bearing row sits in one time window — the "1st half" is
n=0 for all four tiers. The cross-sport replication is what carries this
instead of a time split, and that is weaker evidence than both together
would be.

MLB shows nothing here (756 receipts, no tier bucket reaching n=20), so this
is a non-MLB finding.

### Plan

1. **Do not act on it as a selection rule yet.** "Publish COVERAGE instead
   of STRONG" is exactly the kind of inversion this repo has been burned by
   (SP+ K=0.85, the retracted NFL prop edge, classes_boost). It needs a
   forward sample.
2. **Start the forward sample now.** Tier is already stamped on every
   receipt, so the measurement runs itself — re-measure in two weeks on
   picks made after 2026-10-28 only, which also excludes the signal-weight
   corrections shipped today.
3. **Meanwhile, stop trusting tier for stake sizing.** `ensemble_scorer`
   already carries this warning in its own conviction comment: "conviction
   still does not rank pick quality and must not be trusted for stake sizing
   until `base` has its own study." The same is now true of tier, and
   `TIER_UNITS = {'PRIME': 2.0, 'STRONG': 1.5, 'LEAN': 1.0, 'COVERAGE': 0.0}`
   in `compute_surface_records.py` is staking in exact inverse proportion to
   measured outcome.
4. ~~Check whether the COVERAGE bucket is mostly MC-dissent picks.~~
   **DONE 2026-10-28, ruled out.** Only 15 of 277 graded COVERAGE receipts
   (5.4%) sit on an MC-dissent game, and COVERAGE with NO dissent is
   **61.5% [55.4, 67.1] +20.82u on n=262** — indistinguishable from the
   bucket as a whole (61.4%, +22.71u). The two findings are independent and
   R18 stands on its own evidence.

### Why the MC tier demotion stays, now for a measured reason

This morning I kept the MC dissent tier demotion and said it "lowers
exposure" — a judgement, not a measurement, and Andy was right to push on
it. The measurement now exists: the demotion moves picks STRONG->COVERAGE
(9), STRONG->LEAN (8), LEAN->COVERAGE (6), and COVERAGE is the
best-performing bucket. The demoted picks went 40-31 (56.3%, +5.43u).

So the demotion lands picks in the bucket that wins. It stays — but note
that is a coincidence of two separate defects, not a working gate, and it
dissolves the moment R18 is resolved.

---

## R19 — CLOSED 2026-10-28. The bridge existed; the pricing path ignored it

Found while measuring R4: of 59 unpriced NFL receipts carrying a `game_id`,
**0 joined** `nfl_game_results`.

### Two wrong turns on the way, both worth recording

**First framing was wrong.** I wrote this up as "NFL receipts cannot join
NFL game data". They join `nfl_game_context` fine — that table is keyed on
the same hash the receipts carry. The split is **context vs results**:

```
nfl_game_context / NFL receipts   7d664fbcd39350e559d83c0068ba7169
nfl_game_results                  20270110_TB_NO
```

**Second probe was a wiring error.** I then queried
`nfl_game_results.context_game_id`, got `42703 column does not exist`, and
the script carried on and printed "0 join via game_id" for 201 NCAAF rows
that do in fact join. The read had failed, so every number in that table
was meaningless — `feedback_zero_rows_is_a_wiring_error` and
`feedback_select_star_before_concluding_absent` in one go. The column name
came from `bridge_football_game_ids.py`'s **stale docstring**; migration
20260921d creates a TABLE, not a column, and deliberately so (a game
accumulates more than one historical id, so a single column is many-to-one
the wrong way round).

### The actual state

`football_game_id_alias` exists, is applied, and is populated:

```
rows 984    NFL 329   NCAAF 655      by source: context 851, line_history 133
```

And it resolves the gap completely:

```
sport   unpriced w/ gid   direct join   VIA ALIAS   of those with a closing ML
NFL            59              0            59               59
NCAAF         201            170           180              153
```

So this was never a missing bridge. The bridge was built on 09-21 and my
`_close_price` fallback simply did not consult it — the same class of
mistake as R4 itself, one layer down.

**Fixed:** `_close_price` now resolves `game_id` through
`football_game_id_alias` when a direct lookup misses.

**Correction to my own first write-up of this.** I recorded it as "0 of 59
becomes 59 of 59". That conflated two different things. The alias resolves
the JOIN for all 59, which is what was measured — but only the moneyline
rows can then be PRICED, because NFL results store no spread, prop or POTD
price column. Of the 59, the market split is rl 24, prop 18, ml 10, potd 4,
total 2. Verified delta after applying: **NFL ml unpriced 24 -> 7 (17
priced)** and NCAAF ml **31 -> 3 (28 priced)**. The bridge was worth wiring;
it was worth 17 NFL prices, not 59.

**One genuine risk this leaves:** an empty join does not error. NFL receipts
missed pricing for weeks with nothing failing. The bridge's own migration
notes the same pattern cost the football money-flow gate its entire
validation (NFL 0/51 flags graded). Worth a standing check that counts
join-misses per sport rather than trusting that a join found rows — filed as
a follow-up, not claimed as done.

## REGISTER STATUS — end of 2026-10-28

Closed today: **R5, R8, R10, R12, R17, R19**, and **R4 partially** (563 prices
recovered and applied; the unrecoverable remainder is enumerated with
reasons rather than carried as an open promise).

Shipped as a measuring shadow rather than a behaviour change: **R2**.

**Time-gated by design — cannot be closed today, and saying otherwise would
be false:**
* **R6** — NCAAF conviction needs ~2 more weeks of graded picks.
* **R11** — MC dissent re-measure at n>=60. The demotion itself was already
  removed today, so the exposure question is settled; only the forward
  measurement remains.
* **R13** — `prop_playbook_decisions` baseline is clean from 2026-10-28, so
  the first honest denominator is ~2026-11-07.
* **R18** — tier/`game_read` needs a forward sample on picks made after
  2026-10-28. Deliberately NOT acted on: "publish COVERAGE instead of
  STRONG" is exactly the inversion this repo has been burned by.

**Blocked on a product decision from Andy, not on work:**
* **R1** — which denominator is the real one (`surface_records` excludes
  COVERAGE/conv-0; receipts counts them; the exclusion drops 84% of NBA and
  3% of MLB). Recommendation on file: count everything that reached a user.
* **R7** — NFL prop volume: no cell clears breakeven and conviction is
  inverted there, so the choice is publish fewer or publish none.

**Open and actionable, not yet done:** R3 (the market-change population is
resolved as "keep the receipt"; what remains is wiring the pre-game check to
`line_history.commence_time`), R9 (measured — two NHL
cohort candidates carried forward, nothing enabled), R15 (the app-side NULL
risk is verified safe; only the "lifetime is ~90 days" labelling remains).

---

# EVENING ADDENDUM — 2026-10-28 · the favourite-bias dive

Andy: *"All NBA reads tonight are just favortie spreads or favorite ML, has
it been like that all NBA season ... why doenst engine pick dawg spreads?
When engine says fav ML how often does Dawg win on spread? Is it like that
for all sports, feel like NFL engine spits ou some +7.5 plays right? ...
Additonally La v LAC in NBA tonight is missing game data."*

## R20 · FIXED · spread picks were priced with the MONEYLINE

Tonight's NBA board, as published:

```
San Antonio Spurs -13.5   shown -650   real spread price -110/-110
Houston Rockets   -8.5    shown -340   real spread price -115/-105
New York Knicks   -6.5    shown -275   real spread price -105/-115
Philadelphia 76ers -7.5   shown -270   real spread price -110/-120
Detroit Pistons   -7.5    shown -295   real spread price -120/-110
Oklahoma City     -5.5    shown -215   real spread price -110/-110
Cleveland         -9.5    shown -110   correct
```

-650 and -340 are *exactly* that game's away moneyline. Breakeven at -650
is 86.7%, so every ROI figure and every juice gate on those picks was
computed against a number no book posts — and a subscriber was shown it.

**Root cause, proven by timestamps.** All 12 reads were priced at
18:37:30 and regenerated at 22:28–22:30, ~4h later. The picks that moved
from ML to spread kept the ML price. `price_picks.py` handles this
correctly already (it re-prices any unstarted game and freezes only after
kickoff — written for this exact failure on 09-24), **but it is not in any
sport pipeline**; it runs only in `nightly_cross_sport.sh`. And the
price-clearing guard added 10-27 lives in `sync_jerry_reads_from_ctx.py`,
which **skips every read a real synthesis generator wrote** — which is all
of them. A guard on one of several writers again.

This was a *known queued item*. `nightly_cross_sport.sh` has said since
10-02: "this is the daily floor, not the ideal ... the right place is also
immediately after each sport's read generation ... Wiring that per-pipeline
is queued." 26 days queued; tonight it was public.

**Done:** re-priced NBA from `line_history` at the pick's own line (13 of
13 written, -650 to -111, -340 to -105); NULLed the 2 NFL rows whose line
no book posts any more (a NULL is visibly absent, a stale number is not);
wired `price_picks.py --sport X --write` immediately after read generation
in **nba / nhl / nfl / ncaaf** pipelines. Impossible prices across all
sports: **8 to 0**.

**Follow-up:** 8 NFL reads sit at lines no book currently posts (HOU +2.5,
IND +1.5, NYG +5.5, SEA -13.5, SF -8.5, BAL -4.5, CIN -5.5, DET -6.5).
That is the 10-19 published-lines finding, still open.

## R21 · FIXED · the NBA line join dropped every Clippers game

LAL @ LAC was the one game tonight with no `close_spread` and no
`close_total` (100 of 116 columns filled vs 111-113 for the rest).

`nba_game_context.enrich_market` matched the odds event with
`home_full in h` and parsed outcomes with `name == home_full`. ESPN says
**"LA Clippers"**; the Odds API says **"Los Angeles Clippers"** — and
"LA Clippers" is **not** a substring of "Los Angeles Clippers", so both
tests missed and a bare `continue` swallowed it. It read as missing data
rather than a failed join.

**Done:** joined on the mascot token, verified unique across all 42 NBA
team strings in `nba_game_results` (0 collisions; Trail Blazers to
blazers, 76ers to 76ers), and any game matching no odds event is now
printed every run. Rebuilt: LAL@LAC now carries spread -1.5, total 224.5,
ML -112/-108. **Tonight: 12 games, 0 missing a spread, 0 missing a total.**
The read itself was generated before the line existed, so its blank line
and price were filled from the market (Over 224.5 at -110) — side
untouched.

## R22 · MEASURED · the favourite bias is real, and sport-specific

Dog share of our own spread picks, `call_line > 0` (full season):

```
sport     n    FAV   DOG    dog%   biggest dog   common dog lines
NFL      94     54    40   42.6%        +13.5    +2.5 +3.5 +1.5 +3.0
NCAAF   401    266   135   33.7%        +46.5    +14.5 +2.5 +9.5
NBA      36     31     5   13.9%         +5.5    +1.5 +3.5
NHL      41     38     3    7.3%         +1.5    (puckline only)
```

Moneylines: **NFL has taken a plus-money ML 0 times in 24 priced picks.**
NBA 3 of 23, NHL 18 of 146.

So Andy is right twice: NBA/NHL are favourite-only in practice (tonight 11
of 12 NBA reads were favourites, 0 dogs), and NFL *does* produce +7.5-type
plays.

**The mechanism, measured.** Our projected margin vs the market's:

```
sport    n    mean|proj|  mean|close|  proj MORE extreme   mean diff
NBA     77         6.92         5.92              61.0%       +1.01
NCAAF  517        10.53        11.97              45.5%       -1.44
NFL    314         2.80         4.44              25.5%       -1.64
NHL    208         0.76         1.49              12.5%       -0.73
```

NBA projects favourites ~1 point **bigger** than the market, so "largest
edge" lands on the favourite by construction. NFL projects **smaller**,
which is why NFL gives dogs. **NHL's favourite bias is NOT explained by
this** (its projection is the least extreme of all four) — cause unknown,
open.

**And the projection loses to the close in every sport**, each measured in
its own sign convention:

```
sport    n     MAE close   MAE projection   MAE constant 0
NBA     63        10.786           12.595           13.159
NFL    105         8.576            9.800           10.133
NCAAF  447        11.254           13.770           16.879
```

Correction to my own first pass: I reported NCAAF projection MAE as 23.595
and "worse than a constant". That was the **sign**, not the model —
NCAAF's `projected_spread` is in margin convention while its
`close_spread` is in betting convention. In its own convention it is
13.770 and it *does* beat a constant. `ncaaf_game_context` already handles
this deliberately (`edge = abs(proj + close)`, fixed 09-16 from Andy's
MIA@WF audit), and NFL correctly subtracts because both its columns are
nflverse convention. **No sign bug — the handling is correct. My
measurement re-derived it.**

## R23 · MEASURED · when we take the favourite, what the dog does

```
sport  market    n   fav ML won   dog covered   95% CI
MLB    ml      275        59.6%         56.7%   [50.8, 62.5]
NCAAF  ml       92        64.1%         48.9%   [38.9, 59.0]
NCAAF  rl      220        79.5%         49.1%   [42.6, 55.7]
NHL    ml      117        60.7%         66.7%   [57.7, 74.6]
NFL    rl       32        50.0%         75.0%   [57.9, 86.7]
NFL    ml       29        82.8%         27.6%   n<30, under gate
NBA    rl       24        83.3%         45.8%   n<30, under gate
NBA    ml       12        58.3%         41.7%   n<30, under gate
```

**NHL looked like the big find and it is not.** Priced at the dog
puckline's *actual* cost: 66.0% on n=106, **mean breakeven 66.6%**, median
price -205, **ROI -0.14%**. Flat. The market prices it exactly. Same trap
as this morning's NHL props — a rate that clears 52.4% and loses at its
own price.

**NFL favourite spreads are the real finding.** Dog covered **24 of 32 =
75.0%**, and split-half it is **75.0% / 75.0%** on 16 each. At the
conventional -110 (NFL results store no spread price column — an
assumption, stated) that is **+43.18% ROI**. Consistent with the known
"engine loses to a one-word rule" and already partly actioned by B209
(NFL spreads refused on both curated surfaces). n=32 is the limit.

## R24 · FIXED · the NBA prop discriminator was never scheduled

`nba_prop_gate.py` (built 10-26, the quintile gate with a 37-point spread
on overs) was run by hand once and never wired. The board decayed straight
back to one flat tier:

```
10-25  signals on 1,006 of 1,110 rows   tier LEAN/SKIP/COVERAGE
10-26  signals on   317 of 1,650 rows
10-27  signals on     0 of   642 rows   tier COVERAGE on ALL
10-28  signals on     0 of 1,945 rows   tier COVERAGE on ALL
```

Cause: `nba_generate_props.py:209` upserts tier COVERAGE / conviction 0 /
signals {} on every run, overwriting anything applied out of band. So
in-run ordering is the fix. Wired **after** the generator. Publishes
nothing — SKIP and LEAN only, and publishing needs PRIME plus a feature
flag that is still FALSE.

## R25 · FIXED · the Dawg receipt dropped a price that now exists

`daily_dawg` gained `price_american` in today's own multi-sport rebuild,
but `public_receipt.dawg_rows` still carried the 10-06 comment "daily_dawg
has no price column at all" and set no `pick_odds`. Tonight: MLB +135, NHL
+117 on the source rows, both receipts NULL.

Fixed in **both** writers, because the receipt upsert is
`resolution=ignore-duplicates` — the builder cannot repair a receipt that
already exists, and the backfill cannot repair one never written. 2 of 2
written, verified by read-back. `market` stays `dotd` (the R3 decision:
the receipt's market is the denominator its record is computed against);
the source's `ml` is recorded in `audit.source_market` instead.

## Still open from the status sweep

* **NHL props**: conviction works (11 distinct, max 97, 39 rows >=70) but
  `signals` holds exactly one key, `two_sided_price`. No evidence trail,
  against the engine-selection standard. Tier COVERAGE on 100% is
  *correct* (R5: -6.93% ROI at real prices) — the gap is the audit trail.
* **NFL props**: plumbing complete (73 distinct conviction, real tier
  spread, 10 signal keys) and **819 receipts in 7 days**, our highest
  volume surface, on a market where no cell clears breakeven (R7). The
  volume is the exposure.
* **Signal firing**: 4 of 27 `isinstance`-gated prop signals have fired
  since 10-25. The other 23 are mostly MLB batter signals on a 2-team
  World Series board and NFL signals with 45 playbook rows — i.e. low
  volume, NOT proven dead. Needs the real test: per signal, count rows
  that *qualified* vs rows that *fired*. A column check passes them all.
* **NBA projection coverage**: `projection` / `proj_vs_line_edge` on 346 of
  1,945 rows (18%). The discriminator feeds under a fifth of the board.
* **NHL favourite bias**: 92.7% of NHL spread picks and 87.7% of its
  moneylines are favourites, and the projection mechanism does NOT explain
  it. Cause unidentified.

---

# R26 — THE SIDE BIAS: measured, and THREE of my own hypotheses killed

Andy, 2026-10-28: *"NBA picking favorite only is unsat, infact most of what
you said was unsat. NBA engine needs serious wokr in elimnatiting bias, we
need to eliminate bias rigth now across eveythign, am i clear? NHL as well
tge engine should be predictive in nature and be basing seleciton on all the
data we have discussed."*

## The tilt, confirmed

Favourite share of our own picks, full season: NBA spreads 86%, NBA ml 85%,
NHL spreads 93%, NHL ml 88%. NFL has taken a plus-money moneyline **0 times
in 24 priced picks**. Tonight NBA was 11 of 12 favourites.

## IS IT EARNED? `audit_side_bias_all_sports.py` (new, writes nothing)

Every pick priced at the closing price of the side we took, against the
one-word rules on the SAME games. Split-half underneath.

```
sport  market     n   fav%    engine   control       gap  holds both halves?
MLB    ml       342    80%    +0.24%    -2.55%    +2.79pp  NO — flips (-12.67 / +9.09)
NCAAF  ml       111    83%    -5.19%    +2.26%    -7.45pp  yes (-22.56 / -2.66)
NHL    ml       137    85%    -0.03%    +1.42%    -1.46pp  NO — flips (-6.70 / +3.71)
NFL    spread    65    48%   -23.64%   +23.36%   -46.99pp  YES (-59.66 / -34.71)
NCAAF  spread   326    67%    -8.64%    +1.90%   -10.54pp  YES (-12.88 / -15.23)
NHL    spread    53    81%   -13.55%   +11.66%   -25.21pp  NO — flips
NBA    ml        18 / NBA spread 29 / NFL ml 29 / MLB spread 18 — all n<30
```

**Robust: NFL spread and NCAAF spread.** The engine is anti-predictive
against a one-word rule in both halves. NCAAF ml is negative in both halves.
NHL ml, NHL spread and even MLB ml FLIP sign between halves — not callable.

So the NBA/NHL favourite-lock is a proven DESIGN defect (86-93% one-sided)
whose COST is not yet measurable. That matters: it rules out fixing it by
inverting toward dogs, which would be an unvalidated flip. And
`audit_home_away_signal_bias.py` (10-10, Iowa @ Washington) already measured
that forcing more road picks makes NCAAF *worse*.

## THREE HYPOTHESES OF MINE, FALSIFIED BY MEASUREMENT

**1. "NBA picks rest on ONE chip, and it is an Elo favourite proxy." WRONG.**
The stored trail shows mean 1.19 chips on NBA with `nba_elo_home_ml_strong`
24 of the firings — but that trail is lr_v1 DAMAGE, not the engine.
`project_lr_v1_override_erases_attribution_1027`: lr_v1 rewrote
`primary_play` on 457 picks and destroyed `_ensemble_sources`. Re-run fresh,
Detroit's ML carries FIVE chips:

```
home_ml_hot_at_home      team_form      n=4    +0.28
external:dimers          external_pick  n=40   +0.20
external:oddscrowd       external_pick  n=35   +0.19
nba_elo_home_ml_strong   model          n=0    +0.18
h2h_home_dominant        h2h            n=0    +0.13
```

I built a price-proxy PASS gate on the strength of that wrong reading. Kept
as a backstop — it is correct behaviour if a pick ever rests only on proxies
— but stated plainly: **it fires 0 times on a fresh board.** It is a guard,
not the fix.

**2. "The signal library is structurally home/favourite-biased." WRONG.**
home-capable : away-capable is 1.14 MLB, 1.12 NFL, 1.07 NCAAF, 1.11 NBA,
1.08 NHL. The `__fade` signals are balanced too (NBA 1 HOME / 1 AWAY, NHL
2/1, NCAAF 2/2). The library CAN name a dog.

**3. "The unmeasured-signal floor is the bias engine." WRONG — and this one
I measured before shipping it.** `edge_weight` gives an unmeasured signal a
flat 0.20 while a measured one 1pp over breakeven earns 0.124 and a measured
LOSER earns 0.0 — so being unmeasured beats being measured-mediocre, which
is `feedback_unmeasured_signal_must_not_outweigh_measured` still live in the
code. It is real, and it is NOT what drives the tilt: re-scored at floors
0.20 / 0.12 / 0.06 / 0.02, NBA stays at 11 favourites (91.7% -> 100%) and
NHL at 100%. Made env-configurable and documented, **default unchanged**,
because a scoring change affecting every sport must be backtested first.

## WHAT IS ACTUALLY TRUE

The skew is in which conditions FIRE. Our signals describe form and quality
("home dominant at home", "H2H dominant", "away cold on road"), and the
better team is usually the favourite. Measured firing skew: NBA 80%
favourite, NCAAF 77.8%, NFL 70.4%, NHL 64.5% — from a balanced library.

And nothing corrects for it, because almost nothing is measured. Enabled
GAME signals carrying a measured hit rate:

```
MLB    11 of 117
NCAAF   1 of  81
NFL     0 of  81
NBA     0 of  32
NHL     0 of  38
```

NBA and NHL select with **zero measured evidence**. That is exactly
`feedback_engine_selection_standard` — "MLB got good this way; no other
sport ever got the work."

## THE ONE LEVER THAT MOVED IT, and it is R2

Detroit's ML: 4 contributing classes, and `external_pick` alone holds
0.65 of a 1.24 total = **52.4% of the score**, against a `MAX_CLASS_SHARE`
of 0.40 that IS feasible at 4 classes. That is this morning's R2 cap bug,
and the two chips doing it are `external:dimers` and `external:oddscrowd` —
money-flow followers, which follow favourites. **An uncapped external class
is a favourite vote wearing a measured sample's clothes.**

Applying the fixed-point cap (the math shipped as a shadow this morning):

```
NBA   capfix OFF  11 fav / 1 dog   91.7%
NBA   capfix ON   10 fav / 2 dog   83.3%   LA Clippers -1.5 -> Los Angeles Lakers ML
NHL   unchanged — its picks have <=2 contributing classes, so the 0.40 cap
      is arithmetically impossible (R2's 391-of-464 case)
```

Behind `ENSEMBLE_APPLY_CAP_FIXPOINT=1`, **still off by default**: it
re-scores live picks while `tier` is measuring anti-predictive on game_read
in four sports (R18). Turning it on is Andy's call, not a comment's.

## THE HONEST PATH TO AN UNBIASED ENGINE

1. **Measure the signals.** This is the bottleneck and everything else is
   downstream of it. Attribution now covers all 6 sports, but joined
   firings are NBA 55 / NHL 138 total. No reweighting is possible against
   nothing.
2. **Weight by edge over the direction-only control, not raw hit rate.** A
   "home is hot" signal that fires on favourites must beat "always
   favourite". Raw hit rate REWARDS the tilt, because favourites win ~60%.
3. **Cap the external class correctly** (R2 fixed point) — measured above
   as the only lever that moved the board.
4. **Then** revisit the unmeasured floor, with a backtest.

NOT on the list: forcing dog picks. Measured to make NCAAF worse (10-10),
and the NHL/NBA cost signal flips split-half.

## R26 — WHAT WENT LIVE (end of 2026-10-28)

Andy's calls: enable the cap fix for all sports; next effort = build the
signal measurement chain.

**LIVE, each measured on real boards before flipping, each one env var to
revert:**

1. `ENSEMBLE_APPLY_CAP_FIXPOINT=1` — enforce `MAX_CLASS_SHARE` to a true
   fixed point. NBA favourite share **91.7% -> 83.3%**; `LA Clippers -1.5`
   becomes `Los Angeles Lakers ML`. MLB's PRIME moneyline byte-identical,
   NCAAF sides unchanged (one conviction 74->70), NHL unaffected (<=2
   contributing classes, cap arithmetically impossible). No pick lost.
2. `ENSEMBLE_SIDE_BASELINE=1` — a signal must beat a blind bet on its own
   side. Measured on five boards: **zero side changes**, favourite
   convictions down 0-2 points.
3. `_SIDE_BASELINE` measured for all five sports (was NFL/NCAAF only, and
   both of those values were wrong).
4. The direction-only control supplied to the **attribution path** of
   `refit_signal_registry` (it existed only on the primary_play path), and
   `_recommended_weight_for` now clears the harder of price breakeven and
   side baseline — the tier and the weight had been using different bars.
   `nba_elo_home_ml_strong`: **DISCOVERY w=0.3215 -> UNVALIDATED w=0.2687**.
5. `refit_signal_registry` wired into the **NBA and NHL** pipelines. It ran
   only from the MLB workflows, and MLB is in the World Series — when it
   stops, those two sports would never refit again.
6. `nba_prop_gate.py` wired after the generator and applied: 13,013 rows,
   0 failed. NBA props now COVERAGE 8,080 / LEAN 3,513 / SKIP 2,427 with
   signals on 1,811 of tonight's 1,945 (was 0).

**NOT claimed:** the bias is not fixed. NBA and NHL still have 0 of 32 and
0 of 38 enabled game signals with a measured hit rate. Everything above
makes the measurement CORRECT so volume de-biases instead of entrenching;
it does not substitute for the volume. NBA has 7 days of season at ~8
firings per signal, under the n>=10 floor.

**Still open, named:**
* NHL's favourite lock (93% spreads / 88% ml) is NOT explained by the
  projection mechanism — cause unidentified.
* NFL spread **-46.99pp** and NCAAF spread **-10.54pp** vs a one-word rule,
  both halves. Highest-volume football surfaces, strongest evidence, only
  partly actioned (B209).
* 8 NFL reads sit at lines no book currently posts.
* 8 receipts froze an impossible spread price and cannot be repaired
  (`pick_odds` freezes on first write); the guard now refuses them at
  `sanitize()`, which both write paths call.

## R27 — PUBLISHED vs ENGINE, tonight. The flags are not the story

Andy: *"What pkcks would ahve chnagedntonihgt vs what is published ti bnsuers"*

**What tonight's two flag changes would have changed: 1 pick of 19.**

```
Los Angeles Lakers @ LA Clippers   LA Clippers -1.5  ->  Los Angeles Lakers ML
```
Plus 6 conviction trims: NBA DEN 70->67, PHI 67->62; NHL EDM 82->81,
COL 97->93, WAS 84->82, TOR 62->63 (up); NCAAF MTSU 74->68. No other side
moved on any sport.

**But published differs from the engine's current view on 11 of 19, and the
flags are not why.** Three separate causes, all pre-existing:

### 1. lr_v1 authored 8 of 19 published picks, and promoted 5 from COVERAGE

```
MLB    1 of 1     NHL  3 of 4     NBA  3 of 12     NCAAF  1 of 2
```
`audit_note` on five of them reads `LR override · p=0.6x · was COVERAGE/ml`
-- lr_v1 publishing picks the ensemble had rated COVERAGE, i.e. info-only
and not card-eligible. This is `project_lr_v1_override_erases_attribution_1027`,
where lr_v1 measured **42.0% against the ensemble's 57.1% (n=50)** on
judgeable side flips.

Trail erasure is partly repaired: `_ensemble_sources` is wiped to 0 chips on
tonight's NHL lr_v1 picks and on NCAAF 10-31 (15 of 15), but rows written
10-29 onward keep their chips. So 3a7e38aa fixed the trail going forward and
did NOT stop the override.

### 2. Three published picks are the OPPOSITE side of the engine's view

```
MLB  World Series   published Tampa Bay Rays ML (lr_v1, p=0.62)
                    ensemble now: Milwaukee Brewers ML, conv 84
NHL  TOR @ SEA      published Seattle Kraken ML, CONVICTION 0
                    ensemble now: Toronto Maple Leafs, conv 63
NCAAF KENN @ MTSU   published Kennesaw State +3.5
                    ensemble now: Middle Tennessee ML
```

The NHL one is its own defect: conviction **0**, and its own audit note says
the gate refused it -- *"NHL ML confidence gate: home wp 0.523 < 0.56: that
band is 11-18 (37.9%)"*. The engine declined and we published it anyway,
which is exactly what 44fb3ef7 was meant to stop. Still live.

### 3. 11 of 12 NBA picks published tonight are tier COVERAGE

Published as game reads regardless. Their own notes say why the engine has
no opinion:

```
ensemble_scorer v2 · NBA · 1 sources · score=0      x3
NBA LR sees coin flip (p=0.49) — legacy demote      x4
NBA LR ML too juicy (>-300) — coverage · rerouted   x2
LR override · p=0.6x · was COVERAGE/ml              x3
```

Only Detroit carries STRONG, and that is lr_v1-promoted from COVERAGE. So
the honest answer to "what would have changed" on NBA is *very little*,
because the engine barely has an opinion to change -- consistent with 0 of
32 NBA game signals having a measured hit rate.

**Caveat stated plainly:** the fresh recompute is not automatically "right".
The published pick was made earlier, on less complete data, and the pick
lock preserves it deliberately -- that is correct behaviour, not a bug. What
is a bug is lr_v1 overriding a declined pick, and a conviction-0 pick
reaching users.

**Next actions, not yet done:** make lr_v1 MERGE rather than replace (the
10-27 recommendation, still open); and refuse to publish a read whose own
gate declined it.

## R28 — WILL TONIGHT'S ENGINE CHANGES MOVE RESULTS? No. Measured.

Andy: *"Will this chnage things signifcalty, good or bad coin flip?"*

**The cap fix is a COIN FLIP on outcomes.** The test it rests on: picks whose
class-share cap was violated had one class dominating. Did they do worse?

```
pooled   VIOLATED 46.9% [36.4,57.7] -9.63%  vs  HELD 55.8% [48.1,63.1] +2.91%
NCAAF    VIOLATED 51.5% n=66        -2.31%  vs  HELD 51.5% n=130       -2.39%
NFL      VIOLATED 27.3% n=11                vs  HELD 65.5% n=29
```

In the only sport with an adequate sample the gap is **exactly zero**. The
pooled -8.8pp is a sport-mix artifact plus an n=11 NFL cell, and the pooled
CIs overlap anyway. `feedback_tier_mix_reverses_the_sign`. lr_v1-authored
rows excluded (454) because their chip lists are damage, not evidence.

So: it reduces the one-sidedness (NBA 91.7% -> 83.3%, measured on the board)
and it is a real correctness fix, but there is NO evidence it improves win
rate. I should not have called it "the lever". The side baseline is likewise
0 side flips and 0-2 conviction points. **Neither will move the needle.**

**WHAT IS BEST-EVIDENCED: stop lr_v1 replacing the ensemble.** Measured
fresh tonight on `_pre_lr`, where lr_v1 flipped a moneyline side so both
opinions are judgeable against one outcome:

```
sport    n   lr_v1 right   ensemble right      gap
NCAAF   20         35.0%            60.0%   -25.0pp
NHL     19         36.8%            63.2%   -26.3pp
NFL      8         75.0%            12.5%   +62.5pp   <- inverts, n too small
ALL     47         42.6%            53.2%   -10.6pp
```

Two independent sports, same direction, same magnitude, **replicating the
10-27 measurement (42.0% vs 57.1%, n=50) on a different slice**. Each cell
is under n=30 so none is individually callable by our own gate, but
replication across two sports plus the prior n=50 is the strongest evidence
available for any action we could take.

It is also the biggest surface: lr_v1 authored **8 of 19 published picks
tonight** and promoted 5 from COVERAGE. NFL must be excluded from any
restriction — it inverts.

**Ranked by evidence, what else to do:**
1. lr_v1 MERGE not replace, on NCAAF + NHL only (above).
2. NCAAF spreads: engine -10.54pp vs always-HOME, holds both halves. NFL
   spreads -46.99pp already partly refused by B209; NCAAF is not.
3. Publishing discipline — a conviction-0 read whose own gate declined it,
   and 11 of 12 NBA picks published at tier COVERAGE. A trust claim, not a
   performance claim.
4. Measured signals (NBA 0 of 32, NHL 0 of 38). The real fix; needs weeks.

## R29 — ITEMS 2 AND 3: one dissolved, one is not shippable on evidence

Worked down the list Andy approved. Item 1 shipped (see 1ea05257).

### Item 2 — NCAAF spreads: DISSOLVED BY ITEM 1

The -10.54pp gap was measured on ALL NCAAF spread picks. The rerouted
heavy-fav MLs ARE spread picks in that population:

```
ALL spread picks      n=341  ours 50.1%  -4.27%  vs always-HOME +8.05%  -12.32pp
REROUTED (blocked)    n= 49  ours 36.7% -29.87%                         -35.06pp
NATIVE spread picks   n=292  ours 52.4%  +0.03%  vs always-HOME +8.53%   -8.50pp
  native first half   n=146                                            -14.38pp
  native second half  n=146                                             -2.62pp
```

Removing the reroutes takes NCAAF spreads from -4.27% to **breakeven
(+0.03%)**, and the residual gap **fails split-half** (-14.38 -> -2.62).
**Correction: my "holds both halves" claim was measured on the population
that included the reroutes.** No further action justified.

NFL, same test: native picks lose in both halves (-12.50% / -16.00%, n=49)
-- that part holds -- but "always-DOG +23.17%" is one half carrying it
(+3.41% then +29.82%). B209 already refuses NFL spreads on both curated
surfaces; extending it to game_read is a product decision, not an evidence
gap.

### Item 3 — NOT shippable. Conviction-0 picks are not losing

```
jerry_reads conviction 0   228 published, 41 graded  win 56.1%  ROI +6.17%
receipts    conviction 0   143 reached users, 52 graded  win 51.9%
```

Suppressing them would not improve results. The declined-then-published
subsets by gate reason:

```
gate reason        published  graded   win%
coin flip                 88      55  56.4%
too juicy                 75      46  52.2%
confidence gate           26       6  66.7%   n too small
```

**AND THE ROI COLUMN ON THOSE IS UNUSABLE — caught before quoting it.**
NCAAF "too juicy" printed -100.00% ROI next to a 50.0% win rate, which is
arithmetically impossible. Cause: only **5 of 20** rows carry a price, all
5 lost, so the ROI is computed on 5 rows while the win rate is on 20.

### THE REAL BLOCKER, surfaced by items 2 and 3

**We cannot evaluate these selection decisions because most rows have no
price.** 1,431 graded receipts are still unpriced (down from 1,949 today).
Every suppression question -- items 2, 3 and the R7/R1 decisions -- needs
ROI, and ROI needs a price on each row. Win rate alone cannot settle them:
"coin flip" picks hit 56.4% and still plausibly lose money on juice.

So the honest next priority is NOT another selection change. It is finishing
the price backfill, which is mechanical and already half done. Then items
2 and 3 become answerable instead of arguable.

## R30 — THE PRICE BACKFILL IS AT ITS CEILING. Historical ROI is not recoverable

Andy: *"LEts do it now"* — finish the price backfill so items 2/3/4 become
answerable.

**Result: exactly ONE more row was recoverable.** Not 1,400. The backfill's
own dry run, with every source it knows, enumerates why each remaining row
stays NULL:

```
321  line_history has no priced capture for this game/market/side (pre-10-14)
102  no price source exists for that surface/market at all
 83  the receipt's own pick_side / pick_line is NULL -- and those columns are
     FULLY FROZEN by migration 20260918b, so they can never be filled
 66  POTD row never recorded a price
 59  results row has no closing ML
 54  source row has no price
 22  dawg row has no price (daily_dawg.price_american only added 10-28)
 30  prop row not found at that line
```

**Why it cannot be pushed further — the sources never captured it:**

```
mlb_pipeline_props, share of rows carrying a book price, by month
  2026-04  0%    2026-07  70%    2026-10  100%
  2026-05  0%    2026-08  93%
  2026-06 66%    2026-09 100%
```

Verified on a specific row: "Spencer Arrighetti outs_under 17.5" 2026-08-27
EXISTS in `mlb_pipeline_props` with `book_over_odds` and `book_under_odds`
both NULL. The identity match works; there is simply no price to copy.

Same for the other buckets: MLB/NFL/NCAAF have NO spread or total price
column anywhere (only NBA context and NHL context+results do), and 39 of 56
unpriced NHL rows sit on a results row with no closing ML stored.

### THE CONSEQUENCE, stated plainly

**Historical ROI cannot be computed, and no amount of work will change
that.** Items 2 and 3, and the R1/R7 decisions, cannot be settled on
pre-10-14 data. This is a hard limit, not a backlog item. Every ROI figure
quoted on a pre-October subset is computed on whatever fraction of rows
happened to be priced -- which is how NCAAF "too juicy" printed -100.00% ROI
from 5 of 20 rows (R29).

### WHAT WE DO CONTROL: forward coverage is 96.9%

```
receipts 2026-10-14 .. 10-28:  2,142 written, 2,075 priced = 96.9%
the 67 misses:  POTD 41  ·  NHL game_read rl 13  ·  other 13
```

POTD is the dominant forward gap and it is getting WORSE, not better:

```
daily_best_bet_history odds coverage   2026-08 82%   2026-09 73%   2026-10 32%
```

Three recent graded POTD picks carry no price at all: Milwaukee Brewers ML
(Win, 10-23), Logan Henderson Over 14.5 Outs (Loss, 10-24), Over 7.0
(Win, 10-26). POTD is a headline surface whose record cannot be ROI'd.

**Fixed tonight (e6fe5b0f):** `_potd_odds`, written earlier the same day,
selected `price_american,call_market` from jerry_reads and then ignored
`call_market`. A prop POTD would have taken the game's moneyline price --
tonight that is **+136 against the prop's real -105**, so a win would have
been booked at 1.36u instead of 0.95u. The fallback now refuses a market
mismatch, verified four ways on tonight's game.

### THE PRIORITY THIS CHANGES

Stop trying to reconstruct the past. The honest plan is:
1. Hold forward coverage at ~100%. POTD is the one real hole left; the
   market-match fix is in, and it needs a REAL POTD pick to verify in
   production (10-27 and 10-28 were both "No Play", correctly unpriced).
2. Measure items 2, 3 and R1/R7 on picks from **2026-10-14 forward only**,
   and say so whenever a number is quoted.
3. Accept that the first honest ROI window for those decisions is weeks
   away, not tonight.

## R31 — SMOKE TEST, and WHERE TONIGHT'S CHANGES ACTUALLY BITE

33 checks across every path changed on 2026-10-28, all passing: imports,
both live flags, the full defensive-gate chain on all five sports, the shared
`sanitize()` guard, `dawg_rows`, the refit's two control paths, the POTD
price resolver, and the NBA mascot join.

### Correcting my own commit message on 28783a34

Two of the three refit changes are **inert on current data**, and I should
say so rather than leave the commit standing:

* `_recommended_weight_for`'s new baseline bar **never binds.** It takes
  `max(per-market breakeven, side baseline)`, and the per-market breakeven
  already exceeds every baseline: ml 0.5833 against a max baseline of 0.576
  (NCAAF home), rl 0.5238 against a max of 0.516 (MLB away). Correct, but no
  effect.
* The tier relabel has **no effect on weight.** `edge_weight` returns 0.6217
  for VALIDATED, DISCOVERY and UNVALIDATED alike; only ANTI_VALIDATED zeroes
  it. So demoting `nba_elo_home_ml_strong` DISCOVERY -> UNVALIDATED relabels
  it without reducing its influence, and the 09-03 credibility gate still
  passes it on `n=48 >= 15`.

What DOES bite is the third change, the runtime baseline, and it is
substantial. `ensemble_scorer.BREAKEVEN` is a single 0.524, so a home
baseline above it binds:

```
sport  side      baseline   weight no-base -> with-base   delta
NCAAF  HOME_ML      0.576        0.6398 -> 0.2937       -0.346   (-54%)
NFL    HOME_ML      0.563        0.6398 -> 0.4100       -0.230
NBA    HOME_ML      0.550        0.6398 -> 0.5036       -0.136
NHL    HOME_ML      0.542        0.6398 -> 0.5519       -0.088
MLB    HOME_ML      0.535        0.6398 -> 0.5893       -0.051
every  AWAY_ML   0.424-0.465     0.6398 -> 0.6398       +0.000
```

**Home-moneyline signal weight is cut by up to 54%; away signals are
untouched**, because away baselines sit below the 0.524 breakeven. That is
the asymmetric discount the favourite-lock needed.

It moved convictions only 0-2 points tonight because NBA/NHL signals carry
no measured hit rate, so they take `edge_weight`'s floor path where the bar
is irrelevant. **The mechanism is correctly wired and delayed-action:** as
signals earn hit rates through the now-per-sport refit, the baseline
discounts the home/favourite-pointing ones automatically, and the refit's
control stops them reaching VALIDATED in the first place.

So the honest ledger for the night:

```
REAL, MEASURED EFFECT
  reroute block (NCAAF+NHL)    NHL stops betting those games; NCAAF -38.08%
                               spread -> -18.00% ML. Split-half both sports.
  spread prices (-650 -> -111) user-facing, 8 impossible prices -> 0
  Clippers join                12 of 12 NBA games now carry a close line
  POTD market guard            a prop would have booked at +136 vs -105
COIN FLIP ON OUTCOMES
  cap fixpoint                 NBA 91.7% -> 83.3% favourite, but the
                               cap-violation test is 51.5% vs 51.5% in NCAAF
DELAYED ACTION, CORRECTLY WIRED
  side baseline                -54% on NCAAF home-ML signal weight, muted
                               until signals are measured
  per-sport refit + control    first weights accrue over the coming weeks
INERT (correct, no effect)
  refit weight bar, tier relabel, proxy-only PASS gate, unmeasured floor
```

## R32 — ITEMS 1, 2, 3 worked in order

### Item 1 · NHL favourite lock — EXPLAINED, and one defect fixed

It was logged "cause unidentified". The chain, measured end to end:

**The triple count (fixed).** "The away team is on a back-to-back" and "the
home team has 3+ days rest while its opponent has 0" are the same comparison
from both ends, and NHL carries THREE signals for it across two classes, so
neither the family table nor the 10-02 mirror-norm collapsed them:

```
nhl_away_b2b_fade         situational  HOME_ML  +0.32
away_b2b_penalty          model        HOME_ML  +0.11
home_long_rest_advantage  model        HOME_ML  +0.08
```

Over 116 stored NHL trails, **12 picks carry 2+ of them and on those they
average 70.5% of the entire score**, with FOUR picks where they are 100% of
it. Every instance is the away team tired, so every instance votes HOME.
Grouped into a `rest_b2b` family. Effect tonight: Colorado **PRIME 93 /
score 2.03 -> STRONG 88 / 1.49** (a stake-band change); Washington
0.94 -> 0.92. NFL duplicates it too (`nfl_away_rest_edge_2plus` +
`nfl_rest_advantage_away`).

**But that is not the whole cause, and no TOP pick flipped.** The engine DOES
produce the dog — on every measurable game the RL candidate IS the dog
puckline and WINS its own market (PHI +1.5, NJ +1.5, CGY +1.5, SEA +1.5).
TOP compares scores ACROSS markets, and the evidence is allocated unequally:

```
NHL, 5 dates, 34 games:   ML 73 chips (2.15/game)   RL 34 (1.00)   = 2.15x
```

Rest, goalie, Elo and money-flow signals are written as `*_ML` votes; only
team-form/ATS are written as `*_RL`. The ML chips point HOME/favourite, the
RL chips point AWAY/dog, and score scales with chip count. **So the
favourite moneyline beats the dog puckline essentially always — that is the
93% lock.** Fixing it is a design decision about comparing markets with
unequal evidence. Recorded, not shipped.

### Item 2 · BLOCKED on a migration that was never applied

`reconcile_stale_side_receipts.py --apply` failed on all 47 rows:

```
23505  Key (sport, surface, game_date, source_id)=(NCAAF, game_read,
       2026-10-17, 7743) already exists
voided 0, replacements written 0, failed 47
LIVE receipts still on a stale side: 122 (was 179)
```

All 47 are already VOID, so with 20261025a's partial index the insert
succeeds. It failed, so the full constraint is still live and **20261025a was
written on 10-25 and never applied** — there is no `_apply_` note for it
while every comparable migration has one. Nothing was lost: the script
inserts before it voids, so `voided 0` means it stopped cleanly.

Apply note written. It unblocks 47 replacements, with **133 of 180
candidates deliberately left alone**: 64 market-changed (the R3 decision),
34 MLB with no start-time column, 25 where the read changed AFTER kickoff so
the receipt stands, 10 ungradeable. The repair makes the record WORSE —
47 VOID at +0.00u become 24 LOSS / 23 WIN at **-2.52u** — which is the point;
a repair that improved it would be the red flag.

### Item 3 · NBA projections 17.8% — THREE defects, no missing data source

```
pts     328 props  26.2%      steals  288  0.0%
reb     310        29.4%      pra     264  0.0%
threes  287        32.1%      blocks  222  0.0%
ast     246        31.3%
```

1. **Two spellings of one family.** `base` is `prop_type.split('_')[0]`, the
   board says `steals_over` / `blocks_over`, `PROP_TO_PROJ` keyed them
   `stl` / `blk`. **510 of 1,945 props (26.2%) dropped on spelling alone**,
   while proj_stl sat populated on 566 of 1,976 source rows and proj_blk on
   557 — the same ~28.6% as pts/reb/ast.
2. **The combo families are a sum we already hold.** proj_pra is populated
   on 13 of 1,976 rows (0.7%) while the parts are at 568 each, so `pra`
   mapped correctly and produced nothing on 264 props. A combo projection is
   exactly the sum of its parts (E[X+Y+Z] = E[X]+E[Y]+E[Z] whatever the
   correlation), so the derived MEAN is not an approximation — verified
   Anthony Davis 22.0+10.6+3.1 = 35.7 vs a 34.5 line. Stamped in
   `signals._projection_derived_from`; the variance would need the
   covariance, so this derives the projection only.
3. **Both reads capped at 1000.** Projections asked `limit=1000` against
   1,976 rows; props asked no limit, which PostgREST caps at 1000 against
   1,945. It printed "matched 401/1000" as though 1000 were the slate — same
   family as the prop scorer seeing 22% of an NHL board. Paged:
   **matched 401/1000 -> 1802/1945**.

**And a fourth I nearly shipped:** my own derived-value stamp merged into
`prop.get('signals')` while `signals` was not in the select list, so it would
have BLANKED existing signals instead of merging. Caught before apply.

## R33 — ITEM 2 COMPLETE after the migration, and ONE integrity gap it exposed

Andy applied 20261025a. The repair then completed **without my script** —
and that is the informative part.

`reconcile_stale_side_receipts.py --apply` still reported `failed 47`, but
the replacements already existed:

```
NHL 10-26 #10588    VOID Calgary Flames ML (HOME)  +  LOSS Toronto Maple Leafs ML
NCAAF 10-24 #11675  VOID Louisiana -10 (AWAY)      +  WIN  Southern Miss +10
```

Live ids ~3.97M against void ids 1.1-3.6M, i.e. written after the migration.
The partial index let the **normal receipt writer** insert the correct side —
something it had been attempting and being refused on every run. The
reconciler's "failure" is now a stale complaint about rows that exist.

**Landed state, verified:**

```
game_read receipt keys                      1,829
keys holding void + a replacement              141
   exactly one LIVE beside the void(s)          85   <- the repair
   voids with no live replacement                56
live replacements graded     46 WIN / 37 LOSS / 2 pending
   by sport   NCAAF 37 · NHL 39 · NFL 8 · NBA 1
migration VERIFY: keys with >1 LIVE receipt      0   <- index correct
```

### THE GAP THE MIGRATION EXPOSED

My reconciler guards on "the read was finalised BEFORE the game started", so
a side can never be chosen with hindsight. **The normal writer has no such
guard**, and once unblocked it wrote everything, not the guarded subset:

```
83 of 85   read finalised BEFORE kickoff — clean
 2 of 85   read generated AFTER kickoff
    NCAAF 2026-10-24  Indiana -7.5   LOSS   read 16:05:00  start 16:00:00
    NCAAF 2026-10-24  Rice +8.5      WIN    read 16:03:18  start 16:00:00
```

Both within 5 minutes of kickoff and they cancel (1 W, 1 L), so the record
impact is ~0 — but the principle is breached: those two sides were on record
after the games began. The guard belongs in the writer, not only in the
one-off repair (feedback_guard_belongs_at_the_chokepoint, again).

**Open:** move the kickoff check into `backfill_public_receipts` so a receipt
can never be written for a read finalised after the game started. Until then
the exposure is 2 rows and bounded by how late a read can regenerate.

---

## R34 — WHY NBA AND NHL PROPS HAVE NEVER SURFACED (2026-10-29)

Andy has asked this for over a week. The answer is not plumbing, and it is
not one gate — it is three, stacked, and the third is the real one.

```
1. prop_ensemble_scorer writes ONLY to prop_playbook_decisions   ("shadow mode")
2. promote_playbook_conviction promotes PRIME by default, and
   v_nhl/nba_props_publishable require PRIME or STRONG
3. feature_flags(NHL|NBA, 'pipeline_props') = FALSE
```

Measured today:

| | playbook decisions | live prop tiers | publishable view |
|---|---|---|---|
| NHL | 1,669 → 566 LEAN, 673 BACK | 2,124 rows, **100% COVERAGE**, conv 0 | **0** |
| NBA | 517 → **517 PASS** | 517 rows, 119 LEAN, conv 0 | **0** |
| NFL | 74 → 2 LEAN | — | 499 total, 12 today |

Gate 3 was turned off deliberately on 2026-10-21 and is the binding one.
Flipping it needs **no app build**.

### Should they be flipped on? No. Re-measured, and the answer is the same.

The 10-21 decision was reached with a broken price (see R35), so it was
re-run bet-side corrected and filtered to the -300..+150 band:

```
                   actionable (rec BACK/LEAN), in band
  NHL   n=197   50.3%  vs BE 51.4%   -1.17pp   ROI  -1.09%   split-half -4.91 / +2.52  FAILS
  NBA   n=247   50.6%  vs BE 51.4%   -0.79pp   ROI  -0.16%   every cell negative
  MLB   n=2328  55.3%  vs BE 53.5%   +1.78pp   ROI  +4.03%   split-half +1.52 / +2.04  HOLDS
```

MLB is the only prop surface that clears, and it holds in both halves.
NBA additionally has **nothing to publish even with the flag on** — all 14
measured NBA prop signals are UNVALIDATED or ANTI_VALIDATED, every weight
is ~0, and the scorer PASSed all 517 props today. That is modelling work.

Both flag notes in the DB were corrected on 10-29; NBA's previously claimed
"ZERO graded prop rows" when it has 5,163.

**Status**: ANSWERED. Flags stay OFF on evidence, not on neglect.
**VERIFY**: `python mlb_pipeline/refit_signal_registry.py --sport NBA --dry-run`
(every NBA prop signal, its pick control and its tier), and
`select sport, feature, enabled, note from feature_flags where feature='pipeline_props'`.

---

## R35 — THE PROP SIGNAL PATH GRADED EVERY SIGNAL AGAINST A PICK WE USUALLY NEVER MADE

`collect_prop_firings` in `refit_signal_registry.py`, three defects, each
inverting evidence. Fixed in **e70cffdc**.

1. **`result` grades the prop's LISTED direction.** On a FADE we back the
   opposite, so `result='W'` means our bet LOST. The function already knew
   this (`pb_eff` flipped the side for the baseline) but `won` was read raw.
   NHL 503 of 2,610 graded rows, NBA 818 of 4,540, MLB 2,144 of 25,321.

2. **`correct = (src_side == pb_side) == won` treats PASS as a side.** PASS
   is most of the table — MLB 77.0%, NHL 71.6%, NBA 65.1% — and on a PASS
   row no source can equal `pb_side`, so a signal that voted BACK on a prop
   whose listed direction WON was scored WRONG. Three quarters of the
   evidence, inverted. FADE voters came out right by accident, which is why
   the two families looked so different.

3. **The metric was circular.** 93.96% of MLB firings voted the same way as
   their own pick, so the hit rate was mostly that pick's record wearing the
   signal's name: `pitcher_vs_team_owned` read 70.25% against a 69.42%
   signal-blind control.

A signal's record does not depend on what we did with it: it advocates a
side, and either that side won or it did not. Graded that way the pick drops
out of the metric entirely and PASS rows carry their real information.

### Net effect: 57 of 1,046 registry rows moved

Promoted — all twelve hold split-half with the CI floor above their own side
baseline. This is real edge the inversion was destroying:

```
  pitcher_vs_team_owned             71.90% n=121  +22.36pp   75.0 / 68.9
  pitcher_vs_team_recent_baa_high   68.75% n=144  +20.13pp   75.0 / 62.5
  pitcher_vs_team_career_er_high    68.66% n= 67  +20.00pp   72.7 / 64.7
  pitcher_vs_team_recent_hit_hard   65.50% n=458  +15.96pp   71.6 / 59.4
  pitcher_vs_team_tagged_history    62.33% n=300  +13.71pp   64.0 / 60.7
  pitcher_vs_team_dominant_history  62.40% n=516  +11.54pp   64.0 / 60.9
  player_l10_vs_line_extreme        60.83% n=919  +10.57pp   61.9 / 59.8
```

Demoted:

```
  nhl_prop_l10_extreme   67.19% -> 28.25% n=570  VALIDATED w0.89 -> ANTI w0
  pitcher_xera_elite     57.93% -> 50.43% n=347  VALIDATED w0.46 -> UNVALIDATED
```

`nhl_prop_l10_extreme` is the one that mattered: the only NHL prop signal
carrying real weight, and it is **inverted**. On rows where it disagreed
with a pick we made, our bet won 78.9%; where it agreed, 25.2%. A -53.64pp
discrimination that holds in both halves.

**Caveat to carry**: these hit rates clear a SIDE baseline, not a price.
"65% on n=458" is not a profit claim — the price question is R34's band
table, where MLB `rec=BACK` is +2.91pp / +6.02% ROI.

**Status**: FIXED (e70cffdc). The pick control and the disagreement cell are
now computed and printed every run; `20261029c` adds the columns to store
them and the script probes for them, so no second deploy is needed.
**VERIFY**: `python mlb_pipeline/refit_signal_registry.py --sport MLB --dry-run`
and read the `PICK CONTROL (prop path)` table.

---

## R36 — CLIENT-SIDE SWEEP (2026-10-29) · one real bug, and a class that is clean

**FIXED · The Fade tab never populated on first open** (e70cffdc). Andy:
"i always have to swipe up to refresh page to get picks." The Fade fetch sat
near the END of `fetchSteamFlags`, inside that function's outer `try`, so any
earlier `await` that threw jumped to `catch { setSteamFlags([]) }` and the
Fade lines never ran — empty until a pull-to-refresh re-ran the chain. Even
with nothing throwing it was the last fetch behind a long chain, so the tab
rendered an empty Fade first regardless. Now its own `fetchFade` callback,
called FIRST in the steam effect.

**AUDITED, CLEAN · the silent 1000-row PostgREST cap.** A regex sweep flagged
78 reads with no `.limit()`, which was mostly noise — it was matching
`.upsert()` writes, `{count:'exact', head:true}` counts, and chains whose
`.limit()` sits on the next statement. Every candidate was then measured
against the live row count **with the filters the client actually sends**:

```
  prop_jerry_reads  sport=NFL, 8-day window (what the client sends)     447
  prop_jerry_reads  sport=NFL, NO date filter (not what it sends)     4,666
  external_picks    sport=NFL, +game_id at both call sites          small
  team_recent_games sport=NFL team=GB                                  477
  cleatz_signals    snapshot_date=today                                188
  jerry_reads       game_date>=today                                   174
  v_prop_track_record / _by_type (whole view)                          2 / 8
```

Nothing the client sends today returns over 1000 rows. **Not a bug — but the
margin is thin on `prop_jerry_reads`** (447 of 1000 with NFL alone), so a
`.limit()` belongs there before NBA and NHL props are ever switched on.

**AUDITED, CLEAN · the Hermes `new Date(toLocaleString(...))` reparse.** All
four matches in `app/` are comments warning against reintroducing it.

**FIXED 2026-10-29 (`adecfa8a`) · two of my own tools from the same morning.**
`audit_read_pick_consistency.py` returned non-zero on ANY finding, including
the rules its own comment calls "kept visible but never blocking", so it went
red on NBA x3 / NHL x2 / NFL x2 / NCAAF x1 the same day, every one with 0
BLOCKING - a permanently-red step is a muted step. Blocking codes still exit
2; warnings print and exit 0; `--strict` restores the old behaviour.
`run_step.sh` captured stderr through a process substitution bash never waits
for, so `stderr_tail` was EMPTY on every `step_failed` row it was added to
explain - including those four. Now a FIFO with a real PID, verified capturing
246 bytes with the traceback intact and stdout untouched.

**FIXED · `ExternalPicksPanel` empty state rendered BLACK on a dark card.**
`styles.emptyText` was referenced and never defined, so it came back
`undefined` and the Text took React Native's default colour (#000) on a
`#41586B` card. The one place the panel speaks to a user who has no data was
the one place it was unreadable. Found by grepping tsc for the
undefined-style-key class across `app/` — this was the only instance.
(`feedback_undefined_palette_key_renders_black`.)

**FIXED (preventative) · `.limit(2000)` on the `prop_jerry_reads` client
read**, the only client query measured anywhere near the 1000-row cap.

**Note on tsc as a gate**: `app/` carries **1,841** pre-existing type errors
(mostly `TS7006` implicit-any), so a clean `tsc` is not available as a
pre-build check today. The undefined-style-key class is still greppable out
of that noise with `does not exist on type` and is worth re-running each
build — it is the class that ships invisible text.

**Status**: client sweep COMPLETE for this build.

---

## R37 — THE SIGNAL TOOLTIP SHOWED USERS OUR OWN PICK RECORD (2026-10-29)

`signal_attribution.result` is the **pick's** outcome, stamped onto every
signal row that fired on that pick. `v_signal_records` counted it — and that
view is `GRANT SELECT TO anon, authenticated` and read by the game card
`SignalsRow` tooltip. So every signal displayed our own selection record
instead of its own.

Measured on signals with n>=25 both ways. **11 of 34 inflated:**

```
  sport signal_key                        shown n  shown%   real n   real%      gap
  NCAAF COHORT_NCAAF_HEAVY_HOME_DOG            73   43.8%       70   60.0%   +16.2pp
  NCAAF home_team_ats_hot_season                28   64.3%       28   50.0%   -14.3pp
  NFL   COHORT_NFL_DIV_HOME_COVER               32   46.9%       30   33.3%   -13.5pp
  NCAAF home_ats_cold_at_home__fade             25   52.0%       25   40.0%   -12.0pp
  NCAAF COHORT_NCAAF_HEAVY_HOME_FAV            115   61.7%      110   50.9%   -10.8pp
  NFL   LR_SHADOW                               90   53.3%       82   42.7%   -10.7pp
  NFL   COHORT_NFL_HOME_FAV                     72   48.6%       67   40.3%    -8.3pp
  NFL   OFF_RATING_GAP                          100   51.0%       93   43.0%    -8.0pp
```

The error is **not random**: it penalises a signal for DISAGREEING with the
engine, which is the dissenting/fade voice we most want measured honestly.
Same family as `project_signal_hit_rate_was_our_pick_rate_1028`, which fixed
the refit and the weekly report — and left the view.

**It was about to get worse, not better.** The tooltip's fetch used a bare
`supabase` that threw a swallowed ReferenceError, so these records had never
actually rendered ("signal chips have only ever shown glossary text"). That
was repaired, so the next build would have started showing them.

### Why the fix is a column and not SQL

`signal_outcome.signal_was_right` is the one definition of the rule and it
carries the per-sport close-line sign convention. Reimplementing that in the
view is exactly how `feedback_publishable_view_drift` happens, so instead:

* `20261029d` adds `signal_attribution.signal_was_right` and repoints
  `v_signal_records` at it, counting only non-NULL rows.
* `signal_attribution_grade.py` writes it at grade time.
* `backfill_signal_was_right.py` fills the 4,080 already-graded rows; the
  nightly runs it `--apply --only-null` as a safety net.

Not-countable rows (NEUTRAL subject, cross-axis, push) are NULL and excluded
rather than defaulted. Between the migration and the backfill the tooltip
shows **nothing** rather than something wrong — the correct failure
direction, and the script refuses to run before the migration.

**Status**: **DONE 2026-10-29.** Code `faa60a3f`; `20261029d` applied; backfill
wrote 3,549 of 3,549 and a re-run reports 0 rows would change (idempotent).
Pooled shift 53.1% shown (n=4,042) -> 53.4% real (n=3,549); the pooled number
barely moves, which is the point - the error was per-signal and SIGNED, not a
level shift, so a pooled sanity check would never have caught it. The live
view now matches an independently measured value on **7 of 7** spot-checks:

```
  NCAAF COHORT_NCAAF_HEAVY_HOME_DOG   60.0% n= 70   (was shown 43.8%)
  NCAAF COHORT_NCAAF_HEAVY_HOME_FAV   50.9% n=110   (was shown 61.7%)
  NCAAF home_team_ats_hot_season       50.0% n= 28   (was shown 64.3%)
  NFL   COHORT_NFL_DIV_HOME_COVER      33.3% n= 30   (was shown 46.9%)
  NFL   COHORT_NFL_HOME_FAV            40.3% n= 67   (was shown 48.6%)
  NFL   LR_SHADOW                      42.7% n= 82   (was shown 53.3%)
  NFL   OFF_RATING_GAP                 43.0% n= 93   (was shown 51.0%)
```

**VERIFY**: `python mlb_pipeline/backfill_signal_was_right.py` (dry run; should
report 0 rows would change).

### The measurement layer is now correct on all three paths

With R35 and R37 closed, every path that turns outcomes into a signal record
grades the signal's own advocated side against the actual result:

```
  game path          correct = (src_side == winner)        already sound
  attribution path   signal_was_right(...)                 fixed 10-28
                     kind-rule fallback fires on 11 of 4,080 rows (0.27%)
  prop path          side the signal advocated vs outcome   fixed 10-29 (R35)
  v_signal_records   signal_was_right                       fixed 10-29 (R37)
```

That closes the *measurement* problem. What remains is a **discovery**
problem, which is a different thing and should not be confused for a bug:
most measured signals simply do not have edge. R34 is the clearest case —
every NHL and NBA prop candidate, measured honestly, fails.

---

## R38 — THE BIAS, ROOT-CAUSED; AND REGRESSION, MEASURED (2026-10-29)

Andy: *"picking only favourites is not data driven. regression NEEDS TO BE
ASSESSED!"* Both halves of that, done.

### Why the engine picks chalk — the actual mechanism

Not a one-sided signal library. **The weights reward hit rate, not edge over
price.** `_tier_for` judges a signal against a SIDE baseline, and where none
exists it falls back to a flat `>=55% = VALIDATED`. `side_baseline(NHL,'rl')`
returns `None`. NHL dog pucklines price around -190, **breakeven 65.8%**. So:

```
away_covers_as_dog   65.3% n=98   BE 65.8%   edge -0.54pp   ROI -0.25%
                     -> rated VALIDATED, weight 1.00, the engine's top weight
```

Its hit rate **is** the price. The same mechanism rewards favourite-side ML
signals in any sport where favourites win 60%+. That is the bias, and it is
general, not an NHL quirk.

### Every enabled NHL ML signal vs its own direction-only control

**11 of 12 lose to simply taking the favourite.** The one that decided 6 of
11 picks on 10-29:

```
  nhl_elo_home_ml_strong   68.6% n=70   control "take the favourite" 72.9%
                           ROI +7.6%    control ROI +15.6%     96% chalk
```

All 11 disabled, each with its measurement written to `signal_sources.
description`, reversible via `enabled`. Tonight's published board was NOT
regenerated (games were starting).

**Disabling them alone did not fix it — it RELOCATED the lock.** The board
flipped to 7 of 11 DOGS, driven by `away_covers_as_dog`, the break-even
signal above. So that was disabled too. NHL side picks are mostly no-play
until the tiering fix lands: an honest empty board beats a biased one.

### The fix that matters

`_tier_for` must grade a firing against **the price of the side the signal
took**, not a flat 55%. The prop path already works this way as of today
(R35 uses `market_implied_prob`); the game path needs the same, sourced from
the closing prices already stored on `<sport>_game_results`. One place.

### REGRESSION — assessed, and it is the anti-chalk mechanism

A team whose results lag its underlying process is what the market
underprices. z-scored because the inputs are on different scales (L10
goals/game mean 2.92 sd 0.65 vs xGF/60 mean 2.50 sd 0.44 — a raw subtraction
would be the same-measure error):

```
regression_score = -(z(L10 GF) - z(xGF/60)) + (z(L10 GA) - z(xGA/60))
```

Priced on closing moneylines, leak-free (tendency stamp on/before the game):

```
                                    n    hit     BE     edge      ROI   dogs
  CONTROL always the favourite    195  61.0%  60.4%   +0.58pp   +0.09%    0%
  regression, all games           195  55.4%  53.1%   +2.28pp   +2.82%   43%
  regression, |edge| >= 1.97       70  61.4%  54.9%   +6.55pp  +10.37%   37%
  regression, |edge| >= 2.93       36  72.2%  55.5%  +16.71pp  +32.07%   33%
```

Holds split-half at the seeded threshold: **+8.44pp then +4.66pp**. It beats
the favourite control and **37% of its picks are dogs** where the control is
0%. Two-sided by construction, because it asks who is due to improve rather
than who is better.

Puckline NOT shipped: positive at every threshold (+3.01 to +6.07pp) but
FAILS split-half (+6.08pp then -0.69pp).

Replayed against 301 live rows before seeding: 53 HOME / 40 AWAY fires, 0
eval errors, mutually exclusive, and the replay reproduced the analysis
(n=71, +7.01pp, ROI +11.11%, 37% dogs).

**Status**: `20261029e` written and ready to apply.
**OPEN**: the z constants are fixed numbers over 500 team-games and need a
refresh as the season grows; the honest version precomputes the gap into
`nhl_game_context` as a column. Also open: the same regression test on NBA
(has xG-equivalents?), NFL and NCAAF.

---

## R39 — 86% OF THE ENGINE'S WEIGHTS COME FROM WRITERS WE NEVER FIXED (2026-10-29)

Every correction made to `refit_signal_registry.py` today — the FADE
inversion, the PASS inversion, the pick control, the price-aware bar —
reaches **14% of the rows that carry weight**.

`signal_registry` has **34 distinct `origin` values**. Three of the top four
wrote within an hour of each other tonight:

```
origin                          rows   w>0  VALIDATED  last written
  BACKFILL_2026-10-29            422   419        10   2026-10-29 22:48
  auto_calibration_from_graded   412   180        36   2026-10-29 22:47
  REFIT_SCRIPT                   234   131        32   2026-10-29 23:20
  PROP_AUDIT_2026-10-29           97    86        19   2026-10-29 22:48
  MODEL_PANEL_GRADE_2026-10-29    50    24         5   2026-10-29 18:40
  PROP_BACKFILL_2026-10-29        38    38         0   2026-10-29 22:50
  -------------------------------------------------------------------
  REFIT_SCRIPT share of weighted rows:  131 / 958 = 14%
```

This was found by accident and it explains a lot. A snapshot taken before
tonight's price-aware refit showed values the refit has never produced —
`home_ats_cold_at_home` at 58.4% VALIDATED w=1.00, where the refit computes
41.18% UNVALIDATED w=0; `nhl_prop_l10_extreme` at 73.3% DISCOVERY where the
refit computes 28.25% ANTI_VALIDATED. Those are other writers' numbers,
produced by their own uncorrected arithmetic, on the same column, the same
night. Whichever runs last wins.

This is `feedback_fix_every_writer_to_the_table` at a scale we had not
measured: not two writers, **thirty-four**.

### Why it matters more than any single signal

`tier` is what the ensemble's credibility floor reads to decide whether a
pick may reach a card, and `recommended_weight` is what decides which side
wins a game. Both are being written by scripts that do not know a FADE
inverts the result, that PASS is not a side, or that breakeven on an NHL dog
puckline is 65.8% rather than 52.38%.

### The fix, and it is a design decision not a patch

One of:
1. **Single authority** — `refit_signal_registry` owns `hit_rate`, `tier`,
   `recommended_weight`; every other script writes its own columns or
   nothing. Cleanest, needs an audit of all 34 writers.
2. **Precedence by origin** — the consumer prefers `REFIT_SCRIPT` where it
   exists and falls back otherwise. No writer changes, but the stale rows
   stay visible in the table and will mislead the next reader.
3. **Order of operations** — guarantee the refit runs last. Weakest: it
   leaves the other writers' math wrong and silently depends on cron order.

Recommend (1), staged: start by making the other writers stop touching
`tier` and `recommended_weight`, since those two are what move a pick.

**Status**: OPEN, and it gates the value of R35/R37/R38.
**VERIFY**: `select origin, count(*), count(*) filter (where
recommended_weight > 0) from signal_registry group by origin order by 2 desc;`

---

## R40 — 39 OF 42 MONEYLINE SIGNALS LOSE TO ONE WORD, IN ALL FOUR SPORTS (2026-10-29)

Andy: *"I need to know that every engine is not biased in selection by taking
favorites? NFL looks like alot of favorites even and thats a problem."*

It is every sport, and it is not a tuning problem. Each enabled ml-scope
signal was replayed against real games and graded at closing moneylines
against one control: **take the favourite.**

```
  NHL     11 of 12 LOSE
  NFL      8 of 10 LOSE
  NCAAF   19 of 19 LOSE
  NBA      1 of  1 LOSE
  -----------------------
  TOTAL   39 of 42 LOSE
```

Worst offenders, with the share of firings that point at the chalk:

```
  sport signal_key                       n  fav%     sig     ctl    diff
  NCAAF ncaaf_ret_prod_mismatch_away   118    45   40.7%   82.2%   -41.5
  NCAAF oddscrowd_rl_fade_boost_ncaaf  127    47   48.8%   79.5%   -30.7
  NCAAF away_ml_hot_on_road            111    54   44.1%   72.1%   -27.9
  NCAAF ncaaf_home_underdog_bark        83     1   37.3%   63.9%   -26.5
  NCAAF ncaaf_home_field_baseline      509    70   67.0%   77.8%   -10.8
  NFL   nfl_confluence_home             43    81   51.2%   60.5%    -9.3
  NFL   v3_spread_nfl                   51    92   62.7%   70.6%    -7.8
  NBA   nba_elo_home_ml_strong          43    84   60.5%   67.4%    -7.0
  NHL   nhl_elo_home_ml_strong          70    96   68.6%   72.9%    -4.3
```

Several are chalk by construction and fire constantly:
`ncaaf_sp_plus_edge_home` 100% favourite on 99 fires,
`ncaaf_confluence_home` 93% on 207, `ncaaf_projected_spread` 88% on 338,
`ncaaf_home_field_baseline` on **all 509 games**, NFL `panel_pred_spread`
100%. The libraries are not biased toward favourites; they are *made of*
favourite proxies.

### The nuance that matters

The control is not a money-maker either — NCAAF's favourite control returns
roughly breakeven. So removing these signals does not win money; it stops us
claiming an edge we do not have. **The edge has to come from mispricing, not
from quality descriptions.** A signal that says "this team is good" is a
signal the closing line already contains.

### Regression, re-tested under the right lens, does NOT rescue it

Andy's challenge was correct: testing regression as a standalone picker was
the wrong lens. The right question is whether the gap predicts the margin
**against the spread** — i.e. does it carry information the line lacks:

```
  NHL    n=200  corr +0.0751  CI [-0.0645,+0.2148]
  NCAAF  n=353  corr +0.0816  CI [-0.0232,+0.1863]
  NFL    n=107  corr +0.0018  CI [-0.1904,+0.1940]
  NBA    n= 74  corr +0.1306  CI [-0.1020,+0.3632]
```

All four positive, which is mildly suggestive on its own, but **not one
clears zero**. So the standalone NHL result (+6.55pp on the moneyline) is
most likely reading the moneyline price structure rather than real
information. **20261029e is written but should be HELD, not applied**, until
a forward sample or a clearing residual test supports it.

### What is actually changed, and what is not

```
  CHANGED   NHL: 12 signals disabled (11 ml losers + away_covers_as_dog)
  CHANGED   price-aware bar live in refit_signal_registry (57ad10f7)
            -- but per R39 it reaches only 14% of weighted rows
  NOT DONE  NFL / NCAAF / NBA signals: measured, nothing disabled
  HELD      20261029e (NHL regression) -- do not apply yet
```

Disabling the other 27 would empty the NFL, NCAAF and NBA boards the way
NHL's now mostly is. That is a product call, not a code call.

**Status**: OPEN, and it is the central question of the product.

---

## R41 — WHAT SHIPPED FOR THE 10-31 / 11-01 SLATE (2026-10-29)

Andy: *"we can still fix saturday and sunday nfl ncaaf picks, they cant be
bias like this again this week, it just cant."*

### Two things done, both reversible

**1. Cut 55 signals that lose to the one-word control.** Every enabled NFL
and NCAAF signal was replayed against real games and graded at closing
prices against "take the favourite" (moneyline) and "take the favourite ATS"
(spread). Disabled per market scope so a key that wins on one market and
loses on the other keeps the winning row:

```
             ml-scope        rl-scope
  NFL        8 of 10 cut     9 of 21 cut
  NCAAF     19 of 19 cut    24 of 38 cut
  ---------------------------------------
  55 rows disabled; NFL keeps 100 enabled, NCAAF 67
```

The spread libraries are far healthier than the moneyline ones. NFL keeps 12
spread signals that beat the control, several strongly:
`nfl_penalty_home_undisciplined` +25.0pp, `home_team_ats_cold` +22.6pp,
`away_ats_hot_on_road` +17.1pp. NCAAF keeps 14, best
`ncaaf_home_spread_edge` +16.9pp.

**2. `apply_chalk_cap.py`** — a slate-level gate that demotes the WEAKEST
chalk picks to COVERAGE until the slate is inside a cap. It never flips a
side; flipping would be fabrication. Default max 65% chalk with a floor of 5
side picks per date, both flags.

### Result on the actual board

```
  NCAAF 10-31   18 chalk / 1 dog = 95%   ->  demoted 14  ->  4C/1D = 80%
  NCAAF 10-30+31 combined                    7 actionable, 5C/2D = 71%
  NFL   all dates                            0 demoted - every slate is 1-4
                                             picks, so the floor binds
```

### Why this is a cap and not a cure, stated plainly

The engine has no dog opinions to promote. Every enabled NCAAF spread signal
pointing at dogs was measured and NOT ONE clears the 52.4% an ATS bet needs:
`ncaaf_experience_edge_early_away` 38.2%, `away_covers_as_dog` 43.8%,
`ncaaf_ol_weight_adv_away` 44.2%, `ncaaf_away_spread_edge` 46.9%. The
survivors are home-flavoured (`ncaaf_ol_weight_adv_home` 72% fav,
`home_ats_hot_at_home` 77%, `ncaaf_ground_leverage_home` 75%) precisely
because the away variants are the ones that lose.

So balance cannot be created by promotion without inventing an opinion. It
can only be created by publishing LESS. Publishing 18 chalk picks asserts 18
independent edges; we do not have 18. Five honest picks is a better product
than nineteen padded ones.

### The separate problem this exposed

**NFL 11-01 has exactly ONE actionable pick** (NYJ ML, LEAN 56). The other
11 games are COVERAGE. NFL's board is not chalk-biased — 67% with 8 dogs
across the season, the healthiest of the four — it is nearly EMPTY. Those
two problems need opposite fixes and should not be conflated.

**Revert**: `update signal_sources set enabled = true where description like
'DISABLED 2026-10-29 for the 10-31/11-01 slate%'`, and each demoted pick
carries `primary_play._chalk_cap.was` with its original tier.

---

### R42 — The OddsCrowd spread feed is DARK on both football slates
`line_snapshot` has **0 rl rows for NFL 11-01 (0/12) and NCAAF 10-31 (0/47)**.
That feed is the source the one validated football spread edge was measured on
(fade public bets>=65: 58.9%, n=107, ROI +12.4%, holds both halves — it had
n=183 NFL rl rows historically). With it dark, `apply_dog_floor` has to fall
back to cz/fr splits behind `--allow-cz`, an unvalidated source with its own
documented breakage (10/07). **Restoring this feed is the highest-value item
on this list**: it converts the dog floor from "the engine's own conviction,
unproven ordering" into a measured edge. Check the puller's sport/market args
and whether the NFL rl endpoint changed shape.

### R43 — Two PRIME dog spreads carry `side=None`
`ARI +8.5` (12-06) and `MIA +7.0` (12-27) are tier PRIME with `side: None`.
A pick with a label and no side cannot be graded or reliably rendered, and
PRIME is the loudest thing we publish.

### R44 — NFL stores one market under two names
`primary_play.type` is `rl` on ensemble-written rows and `spread` on 13 others,
all future-dated, all with `conviction=None` and `_model_edge_pp=None`. Two
producers, one market. Any query keyed on one name silently misses the other —
this is what made an earlier dog-spread count come back 7 instead of 9.

### R45 — `consensus_fade_*` is a DIFFERENT fade and reads as the validated one
`detect_consensus_fade.py` fades **handicapper** consensus (75%+ of picks on a
side, gated on audit calibration). The validated edge fades **public money**
from `line_snapshot`. The column names collide, and `consensus_fade_flag` is
NULL on all 13 NFL games anyway. Rename or namespace one of them before the
two get wired into the same scorer.

### R46 — Any script writing `primary_play` must verify the readback
`enforce_pick_lock` refuses a side change with a **204 and zero rows affected**,
byte-identical to success. `apply_dog_floor` reported "promoted 4" and wrote
nothing. `apply_dog_floor.write_play()` now confirms by reading the row back;
every other producer that writes `primary_play` needs the same treatment or it
can report a green step over an empty write.

### R42 — CLOSED 2026-10-29. Root cause: a weekly sport on a daily default
Not a dead feed and not an ID mismatch (100% of snapshot game_ids resolved
against ctx). `compute_align_status_{nfl,ncaaf}` defaulted to **today ET** and
both workflows called them bare; NFL plays Thu/Sun/Mon. `compute_and_write`
emits both `align_status` and `oddscrowd_snapshot`, and `write_line_snapshot`
flattens the latter into `line_snapshot`, so one date default blanked the app's
alignment strip AND went the validated fade dark. Both now take `--days`
(default 8). Coverage 3/179 -> 12/12 NFL, 40/50 NCAAF. Commit de17a96f.

### R47 — cz/fr splits CONTRADICT the validated feed, they are not just weaker
Measured on the same games, share of tickets on the FAVOURITE:
`MIN@DET cz 37 / OC 80`, `LAC@LA cz 70 / OC 41`, `Maryland cz 76 / OC 35`,
`Old Dominion cz 62 / OC 30`. Four of four inverted across the 50 mark. Any
signal built on `splits_summary` needs re-measuring against `line_snapshot`
before it is trusted, and `--allow-cz` on apply_dog_floor should be treated as
a diagnostic switch, not a fallback.

### R48 — OddsCrowd lists 10 games at a time, so a 50-game Saturday is partial
NCAAF coverage is 40 of 50 even with the sweep fixed: the listing page carries
10 links and the probe band walks a few IDs either side. The ensemble-only dogs
on the board are exactly the games with no split to check. Worth paging the
listing or probing by date slug rather than by ID band.

### R49 — The fade is measured on BETS; the feed also carries MONEY
LAC sat at 59% of tickets and 84% of money on the dog — opposite shapes in one
game. The n=107 validation used bets only. Measure the money version (and the
divergence between them) as its own signal before letting either steer a pick.

### R50 — 18 NHL `rl` reads carry no line, 56 of 348 reads carry no price
10-16..10-29. A puck-line read with `call_line` NULL is not a pick and cannot
be graded on its own terms; the 56 unpriced rows (NBA 23, NHL 15, NCAAF 5,
UFC 13) are silently excluded from every ROI number, so the ROI n and the W-L
n differ on the same surface. 12 NBA reads also have `call_market` NULL, and
2026020208 published at **conviction 0**. Either price and line the read at
publish or refuse to publish it.

### R51 — The NBA results step did not run for 10-29
`nba_game_results` held 3 shell rows with no scores — the only played date
since 10-21 missing them — which left 3 jerry_reads and 3 receipts NULL into
the next morning. `nba_resolve_results.py --date 2026-10-29` wrote 3/3 on the
first try, so the data was there all along. Needs the
`feedback_green_step_that_writes_nothing` treatment: assert the output moved.

### R52 — 3 recent `sync_receipt_grades` conflicts need a human
`sync_receipt_grades` reports 544 conflicts, most of them the known frozen-
receipt-vs-mutable-source family. These three are recent enough to matter:
10-17 prop `Nick Martinez Over 3.5 Hits Allowed` (receipt VOID, source LOSS),
05-09 POTD `NRFI` (WIN vs VOID), 05-10 POTD `Under 9.0` (PUSH vs WIN). The
10-28 `Seattle Kraken ML` conflict is CLOSED — the receipt was right and
`daily_dawg` was wrong (commit 1ffd4234).

### R53 — 8 `daily_dawg` rows carry a `final_score` from a different game
Same date-keyed family as the 10-28 cross-sport stamp. 5 are April–June MLB
(two cross-game, three `'Postponed'`), one is 06-06 with a bare `'6-3'`.
Re-derive them with `--regrade-dawg` once the pre-multisport rows' identity
is confirmed.

### R54 — NFL ctx.close_spread disagrees with results.close_spread on 46%
Measured 10-30 on the 100 NFL games where both exist: they match on **54**.
The gaps are not rounding — `NO@NYG ctx -15.5 vs results -1.5`,
`SEA@DEN ctx 7.5 vs results -2.5`, `CHI@ATL ctx -9.5 vs results -1.5`. The
sign convention is NOT the problem (reading ctx as a home margin agrees with
`spread_result` 93/100; as a betting spread, 78/100). So the NFL engine has
been picking against a line that is often not the posted line, which is the
same family as project_jerry_published_fake_lines_1019 but larger — up to 14
points. This also blocks `derive_cover_probability` on NFL, whose self-check
refuses to write below 97% agreement. Find which writer fills
nfl_game_context.close_spread and whether it is a stale open, a different
book, or a team-order bug.

### R55 — nba_elo and nba_four_factors_pull write DIFFERENT seasons
`nba_four_factors_pull` writes the current season (2026-27: pace, eFG, TOV,
ORB, no ratings). `nba_elo train` writes `resolve_season()`, which stays on
2025-26 until 200 games finish, and writes the ratings. So 2026-27 carried
pace on 30 teams and `off_rating` NULL on 30, `nba_mc_simulator.load_team_stats`
took `seasons[-1]`, got no ratings and aborted with "nba_team_stats unusable"
on every run — which is why mc_probabilities was empty on all 21 forward NBA
games and the ladder could not evaluate NBA at all. Loader now walks seasons
newest-first for one that actually has ratings (commit below), but the two
writers should agree on a season rather than being reconciled by a reader.

### R56 — LADDER: tier gate excludes LEAN, which is most of the board
`LADDER_TIER_MIN = {'PRIME','STRONG'}`. On 10-30 every NCAAF pick was LEAN and
every NBA/NHL pick was COVERAGE, so zero candidates existed in any sport — the
ladder was correctly parked, not broken. With cover probabilities derived it
fires on 10-31 (Kansas State ML +102, STRONG, MC 70.3%, edge +20.8pp). Worth
deciding whether a LEAN with a strong MC edge should be ladder-eligible, and
noting that rung's LR p=0.42 contradicts its MC 70.3% — two model numbers
disagreeing on the same pick.

## R57 — the Sweat Card top_8 game pool is MLB-ONLY (2026-10-30)
`generate_sweat_card.fetch_game_context()` is literally
`sb_get("mlb_game_context", ...)`, so `curate_top_8` can never consider a
non-MLB game pick. With MLB at 0 games on 10-30 the top_8 could only ever
hold the POTD. Non-MLB picks reach the card through `fetch_football_picks`
into the `football_picks` section instead, which is why the step logs
"football picks appended: 2 (0 NFL + 0 NCAAF)" — the two were NBA and the
counter only prints NFL/NCAAF. The app prefers `unified_top_picks`
(= top_8 + football_picks), so users were not seeing a 1-pick card, but
every top_8-denominated figure (`top_8_summary`, the yesterday recap) is
MLB-only and understates the record. Fix: give `fetch_game_context` the
same `_in_season_sports()` / `_CTX_TABLE` treatment football already has,
then collapse the two sections.

## R58 — 406 receipts carry NULL pick_odds; POTD and DotD worst (2026-10-30)
Measured over 9,593 receipts since the 2026-09-19 pricing floor:
    market=potd  108 NULL of 141 (76.6%)     market=dotd  21 of 23 (91.3%)
    market=rl    112 NULL of 583 (19.2%)     market=total 39 of 291 (13.4%)
    market=ml     51 NULL of 747 ( 6.8%)     market=prop  70 of 7,641 (0.9%)
ZERO of them are graded Win/Loss today, so no published ROI is currently
wrong — this is a pending defect, not an active one. But any grade that
lands on these prices them at a guess, and a spread assumed flat -110 is
the mistake in feedback_flat_110_inflates_every_ml_figure. Root cause for
the card path: `public_receipt.sweat_card_rows` reads `it.get('odds')` and
the POTD/DotD `add({...})` entries in `curate_top_8` never set `odds`. The
fade entries added 2026-10-30 DO carry `our_price`. Fix every writer to the
table, not one (feedback_fix_every_writer_to_the_table).

## R59 — today's NCAAF game detail disagrees with the Sharp, unfixably (2026-10-30)
`ncaaf_game_context` for 10-30 holds Tulane -8.5 with NO `_flipped_from`
stamp, while `public_receipts` holds a `sharp_card` row for Charlotte +8.5
— the flip of that same game. `flip_football_spreads --sport NCAAF --date
2026-10-30` correctly REFUSES both games ("RECEIPT ALREADY PUBLISHED —
would show one number, grade another"), so today cannot be made coherent
without understating the record. This is the forward fix: the engine flip
has to run BEFORE the composer freezes a receipt, which is what the
2026-10-30 guard in generate_sharp_card does for future slates.

## R60 — recompute_ncaaf_primary_play --lookback 7 reaches ALREADY-PLAYED games (2026-10-30)
`--lookback` defaults to **7**, so `--date 2026-10-31 --days 1` does not mean
"tomorrow". A dry-run on 10-30 reported `changed=65 rerouted=37`, and of the
date-stamped changes in its output **15 were 2026-10-24** (played six days
earlier), 1 was 10-28, and only **3 were 10-31** — the slate that matters.

Under the pick lock this is harmless: `_pp_locked` refuses any game that
already has a `primary_play`, which is all of them. But the whole point of
`NCAAF_PICK_EMERGENCY_UNLOCK=1` is to switch that refusal OFF — and then the
same run rewrites picks for games that have already been played and graded.
That is exactly `project_receipts_grade_the_opposite_side_1028`: the frozen
receipt keeps what we published, `primary_play` becomes something else, and
the two grade different sides.

So an unlocked recompute MUST be scoped: `--lookback 0`, and a forward
`--days` window that stops before the next lock boundary. Better fix: refuse
any game whose `kickoff_utc` is in the past regardless of lookback — a
rewrite of a played game is never correct, locked or not. Same chokepoint
principle as feedback_guard_belongs_at_the_chokepoint.

## R61 — signal_registry.sample_n is EXACTLY 2x the attribution count (NFL confirms) (2026-10-30)
Measured per signal on NFL, own-outcome count from
`signal_attribution.signal_was_right` vs what `signal_registry` claims:

    signal                          own            registry
    power_diff_edge                 44.44% n=9     61.11% n=18
    nfl_offense_mismatch_home_edge  44.44% n=9     61.11% n=18
    nfl_confluence_home             40.00% n=10    55.00% n=20
    v3_spread_nfl                   53.85% n=13    65.38% n=26
    nfl_penalty_home_undisciplined  37.50% n=8     25.00% n=16
    nfl_penalty_away_undisciplined  50.00% n=16    52.94% n=34

n is 2x in every case, and the hit rate is INFLATED in 5 of 6. This is the
same double-count recorded for NCAAF in apply_signal_weight_discipline's
note, so it is not sport-specific — it is the refit. The three NFL signals
whose registry row matches attribution EXACTLY (OFF_RATING_GAP 42.55% n=94,
LR_SHADOW 43.37% n=83, COHORT_NFL_HOME_FAV 41.18% n=68) are the three
already refit from attribution, and all three correctly sit at weight 0.
Fix the refit to dedupe before any weighting decision trusts sample_n.

## R62 — NFL's five "measured" registry signals are dead code (2026-10-30)
`nfl_edge_pct_strong` (51.2% n=1455), `nfl_edge_pct_supports` (51.1%
n=3058), `player_l10_vs_line_extreme` (50.2% n=1539) and
`legacy_conviction_strong` (49.5% n=1440) all carry weight 0.300 and all sit
BELOW the 52.38% spread breakeven on samples of 1,400-3,000. They look like
the worst weighting in the repo — but they have **zero** rows in
signal_attribution and fire on **none** of the 14 forward NFL picks. Their
huge n comes from some other (legacy) backtest, not the per-pick record. So
they are unverifiable rather than actively harmful; do not de-weight on an
unverifiable number, and do not count them as NFL's evidence base either.
The signals that actually drive NFL picks are external:dimers,
nfl_confluence_home, power_diff_edge, nfl_offense_mismatch_home_edge,
panel_pred_spread and v3_spread_nfl.

## R63 — the NFL weight cap cannot reach Sunday while the lock holds (2026-10-30)
cap_unmeasured_signal_weight.py lowered 80 evidence-free NFL game signals
from the blanket 0.300 to 0.153 (the most any evidenced NFL signal holds).
But pick_lock._WEEKLY_LOCK['NFL'] is (Thu 8am -> Mon), so NFL picks are
frozen now and the new weights cannot change this Sunday's board without an
unlock. They take effect naturally in the Tue/Wed generation window. Decide
per week whether an in-window NFL regeneration is wanted; if it is, it needs
the same forward-only scoping as R60.

## R64 — NBA and NHL pipelines were TIMING OUT, not failing (2026-10-30)
NBA had not completed a run since 10-25; NHL since 10-28. Every run between
was CANCELLED at exactly the job cap — NBA 20.3-20.4m against
`timeout-minutes: 20`, NHL 25.2-25.4m against 25. Profiled from NBA run
41101309926 the steps sum to 21.4m of real work with no runaway step
(player props 4.62m, Sleeper enrichment 4.08m, Jerry reads 2.98m, externals
2.10m, L10 backfill 2.10m, grading 2.79m). The caps were set pre-season.
Both raised to 45m. NHL's successful runs were already 21.3-24.4m for days
beforehand, so this was predictable from its own duration trend — worth a
watchdog on "run duration within 20% of the cap".

Second-order: 13.6m of NBA's 21.4m is prop work, and NBA props are entirely
COVERAGE with their two highest-volume signals measured BELOW breakeven
(nba_prop_season_hit_pct_high 47.6% n=3,614; nba_prop_l5_hot 46.8% n=468).
Most of that spend produces nothing publishable.

## R65 — nba_game_context has a trigger that reverts tier on a PARTIAL write (2026-10-30)
Demoting Houston Rockets -6.5 from STRONG returned HTTP 200 and the readback
showed `tier` still STRONG — while the `_retier` audit stamp DID land,
announcing `to: COVERAGE`. So the row carried an audit trail claiming a
demotion the live field never took. nba_game_context has no
`pick_locked_at` column, so this is not enforce_pick_lock; some other
trigger preserves `primary_play->tier` during the daily lock window while
accepting every other key. Same partial-write class as
project_potd_partial_mirror_faked_a_win_1015.
retier_from_sport_bars now restores the row and records `_retier_refused`
(the attempt) instead of `_retier` (an outcome that did not happen). The
trigger itself still needs identifying — a writer that silently keeps one
JSON key is going to bite something else.

## R66 — card_self_contradiction has been CRITICAL since 10-25 (2026-10-30)
This is why "Daily Card (sport-agnostic surfaces)" fails every run. Two real
problems today, neither a false positive:
  * NBA Houston Rockets -6.5 STRONG — projection has Houston by 5.5 while
    the pick lays -6.5. Promoted by retier_from_sport_bars on signal
    agreement alone; the coherence gate added 2026-10-30 now refuses this,
    but the demotion is blocked by the NBA lock (see R65) so it is STILL
    PUBLISHED at STRONG on today's Sweat Card.
  * NCAAF Baylor @ UCF LEAN — pick HOME but projected_spread favours AWAY
    by 1.8. Pre-dates today.
Signal agreement says WHICH SIDE; it says nothing about whether the
projection clears the POSTED NUMBER. Promoting a pick can EXPOSE an
incoherence that was always there, because the watchdog exempts COVERAGE.

## R67 — the chooser: ranked on a scale, now ranked on a record (2026-10-30)
`top_market = max(eligible, key=conviction)` compared convictions ACROSS
markets, and conviction is not the same measure in each. NCAAF's ml MEDIAN
(80) sits above its rl MAXIMUM (65), so an NCAAF spread opinion could never
outrank a moneyline one — ML won 124 of 131 NCAAF tie-breaks (95%), NFL 56%,
NHL 54%, NBA 61%.

Normalising the scale ALONE was simulated and REJECTED: within-market
percentile moved NFL off ml onto rl on 8 games and NCAAF on 48, and at the
time NFL rl measured -8.48pp against ml +6.33pp. An unbiased chooser that
ignores which market wins is measurably worse. MARKET_EDGE_PP now ranks on
the measured per-(sport, market) edge, conviction decides within a market,
and an unmeasured (sport, market) falls back to conviction alone.

Limitation stated rather than hidden: where two edges are close the larger
point estimate wins even though the gap is inside noise (NFL ml n=38 vs rl
n=35). An earlier version bucketed edges onto a 2pp grid to make those tie,
but a fixed grid cannot express "within 2pp" — NFL's +6.33 and +4.66, 1.67pp
apart, landed in different buckets anyway, so the comment claimed a tie the
code did not deliver. Bucketing removed.

## R68 — spread FAVOURITES are the measured loser; dogs are a consequence (2026-10-30)
The favourite bias is not ML-vs-spread, it is which SIDE of the spread.
Graded spread receipts since the 09-19 pricing floor:

    scope      side        W-L      hit%    BE%     edge      ROI
    pooled     DOG         81-66    55.10%  52.83%  +2.27pp   +4.75%
    pooled     FAVOURITE  105-113   48.17%  52.11%  -3.95pp   -8.09%
    NFL        FAVOURITE   10-17    37.04%  52.56% -15.52pp  -29.17%
    NCAAF      FAVOURITE   66-72    47.83%  52.61%  -4.78pp   -9.46%
    NHL        FAVOURITE   12-15    44.44%  48.58%  -4.13pp  -11.10%
    NBA        FAVOURITE   13-9     59.09%  53.34%  +5.76pp   +9.36%  POSITIVE

NFL's rl market reading -8.48pp overall is ENTIRELY its favourites; NFL
spread DOGS are +4.66pp. SPREAD_FAV_REFUSED drops the favourite candidate
for NFL/NCAAF/NHL, so the dog wins the market mechanically. NBA is excluded
because its favourites measured POSITIVE, and MLB has n=4 per side.

Why refuse the favourite rather than promote the dog: the favourite side is
negative in EVERY half of all three sports (NFL -21.95 then -9.55; NCAAF
-6.49 then -3.08; NHL -3.20 then -5.00) while the DOG side FAILS split-half
(-1.15pp then +5.65pp). Removing a measured loser needs no new claim.

Combined effect on the 10-31 NCAAF slate: markets ml 41->30, rl 4->16; side
mix FAV 29 / DOG 17; bettable picks 5 -> 20 (10 PRIME, 10 STRONG).

## R69 — a 28-season validated edge referenced a column that does not exist (2026-10-30)
`ncaaf_high_wind_under` has a CORRECT condition (`ctx.wind >= 15`) and a
BROKEN strength_expr: `min((int(ctx.wind_speed) - 10) / 20.0, 1.0)`.
Neither ncaaf_game_context nor nfl_game_context has a `wind_speed` column —
both call it `wind` — so the expression raised TypeError on every row and
the signal could never contribute strength. This is the one lens validated
on 28 seasons (WIND>=12 -> UNDER, 55.8% CI[52.9,58.6], 17 of 26 seasons).
Fixed to `float(ctx.wind)` and replayed against live rows. The four MLB
signals using `ctx.wind_speed` are CORRECT — mlb_game_context really does
have wind_speed/wind_direction/wind_blowing_in.

Open: audit every condition_expr/strength_expr against its own sport's
columns. A column name that is right for one sport and wrong for another is
exactly feedback_dict_get_on_a_guessed_column, and it fails silently.

## R70 — NCAAB: the chain WORKS, the model has never been graded (2026-10-30)
Earlier I reported NCAAB "cannot produce a pick". Wrong — it was GATED, not
broken. season_gate puts NCAAB off-season through October and flips on
2026-11-01, two days before the 11-03 opener, so nothing was being
exercised. Forced early with `--force-offseason` the chain runs clean: 7
games from the Odds API, 365 teams from ncaab_team_stats, 1,528 aliases,
7/7 upserted.

WHAT IS ACTUALLY MISSING is the validation, not the plumbing:
    projected_total     0 of 11,439 rows populated
    projected_spread    0 of 11,439
    home/away_adj_oe/de 0 of 11,439   (every KenPom field)
    pace_avg            0 of 11,439
    close_home_ml       0 of 11,439
    close_spread/total  5,034 (44%) — ALL of it 2024-25
    2025-26             5,521 played games with ZERO lines
So the projection driving NCAAB's picks has no graded record whatsoever,
and a whole season of results cannot be priced.

MEASURED BASELINE, on the 5,027 games of 2024-25 that carry a close spread,
a close total AND a final score — nothing clears the 52.38% breakeven:
    HOME covers      49.74%  -2.64pp     UNDER hits  49.38%  -3.00pp
    AWAY covers      50.26%  -2.12pp     OVER hits   50.62%  -1.76pp
    FAVOURITE        49.03%  -3.35pp
    UNDERDOG         50.97%  -1.41pp
    DOG either side  51.12%  -1.26pp  (split-half 51.61% / 50.62%)
By magnitude only |spread| 15-25 (+1.43pp) and 25+ (+1.10pp) are positive
and both CIs span breakeven. The ONE cell whose CI clears on either side
clears on the LOSING side: totals posted 155-165 take the UNDER at 45.49%
on n=576, CI[41.5,49.6], upper bound BELOW breakeven.

So NCAAB's 65 signals stay at weight 0 — set there because they were
unmeasured, and the measurement agrees. This is a market with no edge
visible at this granularity, which is what a liquid market should look like.

Open for NCAAB: backfill projected_total/projected_spread for 2024-25 so
the model can be graded at all; find out why 2025-26 has 5,521 results and
no lines; populate the KenPom columns the signals read (several of the 65
reference adj_oe/adj_de, which are 0% — those conditions can never fire).

## R71 — NCAAB totals: one calibration bias restated five times (2026-10-30)
`ncaab_game_context.compute_primary_play` rule 2 is "STRONG OVER/UNDER when
model total vs market differs by >= 5 points". The model sits
systematically 6.7-9.2 points BELOW market, so that rule produced 5 STRONG
UNDERs out of 6 top plays — not five edges, one bias five times. Every one
posted at 154.75-160.5, i.e. inside the 155-165 band measured at 45.49%.
Capped to COVERAGE via NCAAB_TOTAL_TIER_CAP (visible, still recorded, lifts
on evidence). TOP_PICK_BARRED now also holds NCAAB:total, but that gate
only governs ensemble_scorer and this builder has its OWN
compute_primary_play, so the cap had to live in both.

## R72 — DONE 2026-10-30 — APP SCRUB: "kenpom" removed from everything user-facing
Same class as the Madden -> "Sweat Stats" rebrand. Internal column names
(home_adj_oe, adj_em_gap) can keep their lineage, but no narrative, label,
tooltip, signal prose, game-card string or app copy may say "KenPom".
Needs: grep app/ and every prose builder (ncaab_*, analyst_facts,
generate_*_game_reads, signal display_prose, state_message), pick the
user-facing replacement wording, and add a guard so it cannot come back —
the prop-family ban needed three layers before it held.

## R73 — DONE 2026-10-30 — model-consensus note replaced by collapsing the duplicates
ANDY: "look at model concensus block nots that say if the tile s the same it
is one opinion, or somehting to that affect. Why is that note there and i
feel like its confusing to users."
That note is almost certainly the surfacing of
project_panel_is_one_voice_1014 — 27 duplicate model pairs, with NHL and
NCAAF totals at 100% side agreement, i.e. the "panel" was several labels on
one underlying opinion. The note was an honest internal disclosure, but it
reads to a user as the app doubting itself. Decide: either make the panel
genuinely independent (different INPUTS, per
feedback_lens_independence_needs_different_inputs) and delete the note, or
collapse the duplicate voices into one named model so there is nothing to
disclaim. Do not just hide the note while the panel is still one voice.

## R74 — on NBA our read agrees with heavy public money 4 of 4 (2026-10-30)
ANDY: "Wasnt the pick on sweat card magic spread why would primary play be
raptors spread". The proximate cause was the Fade being wired into the Sweat
Card without checking the game read (fixed). The underlying finding is worse.

Every game where the Fade flagged heavy public money AND we hold a read,
compared side by side:

    scope   agrees with public   opposes
    NBA           4 (100%)          0
    NCAAF         7                 6
    NFL           0                 1
    TOTAL        11 (61.1%)         7

    Detroit @ Brooklyn   80% public AWAY | read AWAY (Detroit -8.5)   AGREES
    Orlando @ Toronto    77% public HOME | read HOME (Toronto -5.5)   AGREES
    Lakers @ GSW         76% public AWAY | read AWAY (Lakers -3.5)    AGREES
    Indiana @ Miami      67% public HOME | read HOME (Miami -8.5)     AGREES

On NBA the engine is indistinguishable from public money on these games, so
The Fade is fading OUR OWN read, and the two surfaces are structurally
opposed. That is the same mechanism as the favourite bias — the public backs
favourites and so does NBA's engine — seen from the other side.

NCAAF is roughly balanced, which is what independence should look like.

Next: measure whether the public side actually LOSES on NBA (the fade thesis
is unvalidated there, evidence='tracking'), and whether NBA's read carries
any information the public line does not. If it does not, NBA game reads are
a restatement of the market and should not carry a bettable tier at all —
consistent with the 10-30 finding that the closing line beats both NBA
projections (corr +0.60 vs +0.28/+0.26 on n=69).

## R75 — a frozen label can outlive the missing number (2026-10-30)
4 of 6 NBA spread picks tonight stored primary_play.line = None while the
context row HAD a close_spread, so they rendered as "Toronto Raptors RL" and
"Boston Celtics ATS" — no number, unactionable for a user and ungradeable
for the resolver (public_receipt.unactionable refuses exactly this). Cause:
the label was built BEFORE the odds pull landed, fell through
_label_from_candidate's no-line branch, and the pick lock froze it while
ctx.close_spread filled in behind it. Two fallbacks were visible in the same
column — "ATS" from _market_label and the older hardcoded "RL".
repair_spread_labels.py re-derives line+label from the context row without
touching side/market/tier; 7 repaired (4 NBA, 3 NHL), 0 reverted.
Remaining: 3 NBA rows whose close_spread is genuinely still NULL.
Forward fix needed: do not compose a spread pick until its line exists, or
re-derive the label whenever the line lands.

## R72 / R73 CLOSURE NOTES (2026-10-30)
R72, the scrub. Four live user-facing exposures, all of which would have
started rendering when NCAAB opened 11-03:
  * generate_ncaab_game_reads headline  "KenPom efficiency favors X by Y"
  * generate_ncaab_game_reads tier line "(KenPom + four-factors lens)"
  * sport_registry[NCAAB].state_message "probable starters, KenPom refresh"
  * signal_sources id=924 display_prose_template "(KenPom adj_de ...)"
The app itself was already clean — its only matches are a cache key and
table names. check_banned_vocabulary.py now guards both the live data and
source string literals, exits 2 on any hit, and covers "madden" on the same
rule. Column/table/cache/module names are exempt BY DESIGN: renaming data
plumbing to satisfy a copy rule is how a migration breaks a pipeline.

That guard needed two corrections before it was trustworthy, both recorded
in it: it first flagged 7 signal_registry.notes rows (internal provenance
that nothing in app/ reads) and a function DOCSTRING in ncaab_game_context —
i.e. it contradicted its own stated policy that lineage is exempt. Left
as-is it would have been the permanently-red step in
feedback_a_detector_must_know_the_intentional_case.

R73, the consensus note. It existed to surface
project_panel_is_one_voice_1014 (27 duplicate model pairs; NHL/NCAAF totals
at 100% side agreement), because a reader counting tiles overestimated the
agreement behind a pick. Fixed by collapsing rather than disclaiming:
groupVoices already returns the duplicate -> leader mapping, so models
sharing a number now share ONE tile naming them ("v3 · v4") and the line
reads "N model reads". Nothing hidden, nothing to disclaim.
NEEDS AN APP STORE BUILD — client change, no OTA path.
Still open underneath: the panel is still one voice. Collapsing makes the UI
honest; making the voices genuinely independent (different INPUTS, per
feedback_lens_independence_needs_different_inputs) is the real fix.

## R76 · A VOID HAS NOWHERE TO RECORD WHY — 2026-10-30
Voided receipt 3981345 (the incoherent +622 Daily Degen) plus the three
Sweat Card orphans. `result='VOID'` landed on all four; the REASON did not.
`trg_freeze_receipt_identity` does `NEW.audit := OLD.audit` (20260918b), so
the audit blob is immutable once set — correct design, the receipt is the
record — and 20261025a added void+replace without a reason column. So the
schema can void a play but cannot say why, and four receipts now carry a
bare VOID nobody can explain later.
FIX: either `void_reason`/`voided_at`/`voided_by` columns (new columns are
not named by the freeze trigger, so they stay writable), or an append-only
`receipt_voids` table keyed on receipt id — the latter fits "a void is
history and history should accumulate" from 20261025a's own note.

## R77 · THE FLIP CURE IS ORDERING, NOT THE GUARD — 2026-10-30
1a94baac stops the Sharp flipping a game whose side another surface already
published, which prevents the contradiction but costs the pick. The flip
exists in TWO places: flip_football_spreads.py (source, receipt-aware) and
generate_sharp_card.py (compose). The real fix is to run the source flip
BEFORE any surface publishes, so it lands once for everyone and the composer
copy can be deleted. Until then the Sharp loses these picks to whichever
surface publishes first.

## R78 · THE FADE STAMPS NO INTENT — 2026-10-30
2 of tonight's 4 cross-surface CRITICALs are NBA Fade-vs-our-own-card
(Brooklyn against our Detroit, Indiana against our Miami). The Sharp's
deliberate flips carry `audit.reason` starting "fades our own", so they are
machine-readable as intentional; the Fade's receipts carry `reason=''`. With
no marker there is no way to distinguish "fading the public", which is the
surface's job, from "accidentally opposing our own pick", which is a defect.
Also live: the Fade published Minnesota -7.5 identical to the Sweat Card, so
the same bet is double-counted in the composite record.
FIX: have generate_the_fade stamp what it is fading (public side + pct), then
teach consistency_watchdog to flag only the unmarked case.

## R79 · A FAMILY-WEIGHT REFIT CANNOT MOVE THE ENGINE — 2026-10-30
Measured before building the fitter (measure_family_weight_leverage.py).
Families agree on the side in 931 of 1,076 units (86.5%); a weight can only
change the other 13.5%, and on MLB (0/202) and NBA (0/56) it can change
NOTHING. In the units where it could act the majority vote does not clear
breakeven on its CI: NCAAF 51.0% n=96 CI[41.0,61.0] against a 52.1% blanket
home rate, NFL 58.3% n=48 CI[44.4,72.3]. Following the minority is worse,
44.1% n=145.
Cause: 2,755 of 3,584 attributed signal sides (76.9%) say HOME. The library
almost always says one thing, so the vote has nothing to decide — the same
finding as project_engine_selects_on_zero_measured_signals_1028 seen from
the output end.
NEXT: the lever is evidence that can argue the OTHER side, and every
candidate that has ever cleared is MARKET structure, not team data — the
public-money fade on spreads (+12.4% n=107), the short football spread flip
(62.0% n=121), wind>=12 UNDER (55.8%, 17 of 26 seasons). Give a
market-structure family real weight, THEN re-run the leverage script; if
disagreement has risen the fitter becomes worth writing.

## R80 · line_snapshot CANNOT JOIN NFL RESULTS — 2026-10-30
Measuring the public-money fade per sport: NCAAF joined 995/1000 (99.5%),
NBA 224/224, NHL 174/174, MLB 2013/2025 — and NFL **0 of 58**.
line_snapshot.game_id carries the Odds API hash while nfl_game_results keys
on the schedule id (20260910_NE_SEA), the mismatch
resolve_nfl_results.fetch_result_map already documents and that left every
NFL pick ungraded until 09-11.
So NFL public money is UNMEASURED, not edgeless — do not read its absence
from the fade table as "no edge there"
(feedback_zero_rows_is_a_wiring_error).
FIX: bridge_football_game_ids.py already exists for this family; point it at
line_snapshot, or match on (away, home, week-bucket) exactly as
check_pipeline_health does. NFL is the sport where family disagreement is
highest (42.5%), so it is the one most likely to benefit.

## R81 · THE oddscrowd FADE FAMILY HAS NEVER FIRED ONCE — 2026-10-30
signal_sources class `oddscrowd_direction`: 8 rows, several enabled
(689 MLB ml, 736/737/738 NFL ml/rl/total, 754 NCAAF total, 786 NBA ml).
Attribution rows for ANY of them, ever: **0 of 4,763**. The only oddscrowd
keys in signal_attribution are `external:oddscrowd` (48 rows), a different
family.
NOT the old isinstance bug — the conditions now use the whitelisted
`is_dict` and read correctly. And NOT missing data: oddscrowd_snapshot is
populated on 93 of 123 NCAAF rows since 10-24, with `fade=='boost'` on 24
rl / 21 total / 24 ml games, 50 of 79 NBA, 49 of 94 NHL. So the predicate
is TRUE on real games and the signal still records nothing.
NFL is the exception that may be its own bug: only 14 of 178 rows populated,
so the 10-29 align_status fix did not take for NFL.
NEXT: replay one NCAAF game through the scorer's signal loop with a known
`fade=='boost'` row and find where it is dropped — market_scope='*' against
a per-market gate is the first suspect, since every other family in the
table scopes to a specific market.

## R82 · THE LIVE PUBLIC-MONEY PREDICATE IS THE ONE THAT DOES NOT WORK — 2026-10-30
Two different predicates live in this family and only one has edge.
align_status_common.py:196 sets `fade_note = 'boost'  # money heavier than
bets = sharp`, so the signals fire on money% > bets% (divergence) and
side_expr takes `pick`, the money side. They are FOLLOW signals despite
"fade" in every name, and side_expr is correct for that.
Measured, closing snapshot, graded from {sport}_game_results:
  FOLLOW on divergence >=10pp    NCAAF rl 45.5% n=88  (54.5 -> 36.4) NEGATIVE
                                 MLB rl   50.2% n=227 (54.9 -> 45.6) no
                                 MLB ml   57.1% n=191 (56.8 / 57.3) = ML BE
  FADE on money share >=65%      NCAAF rl 59.2% n=130 (55.4 / 63.1) CLEARS
                                 MLB rl   55.2% n=366 (55.2 / 55.2) CLEARS
So the engine's live family keys on the predicate with no measured support
— negative on NCAAF spreads — while the predicate that clears on spreads
has no signal at all.
NEXT: a spread-only signal on heavy money share, side = OPPOSITE of
pick_side, per-sport (NCAAF + MLB on; NHL OFF, it inverts at 38.9% both
halves; NBA/NFL unmeasured, NFL blocked by R80). Do not pool the two
predicates under one weight — they are not the same evidence.

## R81 RESOLVED · NOT A CODE BUG — AN OFF SWITCH AND AN ORDERING GAP — 2026-10-30
Replayed signal 753 (oddscrowd_rl_fade_boost_ncaaf) through the scorer's own
evaluate_bool/evaluate_str/evaluate_float against live rows. On all 11
NCAAF games since 10-30 carrying rl.fade=='boost' it returns
cond=True, side='HOME_RL'/'AWAY_RL', strength=0.30 — it would emit an
Opinion on every one. The expression layer is healthy; the isinstance fix
took.
It records nothing for two stacked reasons:
  1. enabled=False on 753, the NCAAF rl variant — _load_sources never hands
     it to the loop. The NCAAF *total* variant (754) IS enabled and totals
     measure 48.5% n=198, no edge. The off switch is on the useful one.
  2. For the variants that ARE enabled, the data lands too late. On 64 of 93
     NCAAF rows with both timestamps, oddscrowd_snapshot.pulled_at is AFTER
     pick_locked_at/computed_at — Kent State @ Sacramento State was computed
     10-29 14:12 and its snapshot arrived 10-30 22:03, 32 HOURS later. At
     scoring time the condition is honestly False.
Weight is NOT the blocker: edge_weight returns a 0.20 floor for
hit_rate=None, which is MORE than a measured signal one point over
breakeven earns (0.124) — the favourite-bias floor documented at
ensemble_scorer.py:1006.
FIX, in order: (a) move the oddscrowd/align_status pull BEFORE the scoring
step so the data exists when the engine reads it — same ordering shape as
R77's flip; (b) only then decide enablement, and decide it on R82's
measurement (the enabled variants key on the predicate that does not clear).
Sanity check worth keeping: on Tulane @ Charlotte the signal says HOME_RL =
Charlotte, the exact side the Sharp published via the flip tonight.

## R83 · THE BACKFILL ERASES LIVE RECEIPT PROVENANCE — 2026-10-30
Live prop capture shipped yesterday (26f62394, 10-29, "Prop Jerry gets a
live receipt capture — it never had one"). Since then, prop_jerry receipts
for 2026-11-01 games: **189 reconstructed, 1 live**. Same date, same games.
Leading cause: backfill_public_receipts.py:148 does
`_r.setdefault('capture_mode','reconstructed')` on the PAYLOAD, so every
backfilled row carries 'reconstructed', and the on_conflict upsert
overwrites whatever was stored. capture_mode is NOT protected —
trg_freeze_receipt_identity names only `NEW.audit := OLD.audit`
(20260918a/b), so audit is immutable and capture_mode is not. A live
capture followed by a backfill therefore loses its own provenance.
WHY IT MATTERS: of 19,283 graded prop receipts, 19,282 are reconstructed.
The LIVE record for the whole app is 672 graded bets, and props contribute
ONE of them. Every prop percentage in the product is a reconstruction, and
on surfaces where both modes exist the reconstructed half reads 59.40%
/+5.06% against the live half's 56.00% /-3.65% — so reconstructed rows
flatter, which means the negative NFL prop numbers are if anything
optimistic.
FIX: make capture_mode first-write-wins. Either add it to the freeze trigger
beside audit, or have the backfill omit the column on conflict so it cannot
downgrade an existing live row. Same shape as
feedback_fix_every_writer_to_the_table.

## R84 · I VOIDED TWO PUBLISHED PICKS FOR A BOOKKEEPING REASON — 2026-10-30
Orlando Magic +5.5 (receipt 3981856, section=top_8 rank=2) and Golden State
Warriors +3.5 (3981857, rank=3) were published to the Sweat Card at
21:53:31 and replaced by Minnesota -7.5 at 22:20:35 — live, pre-game, for
~27 minutes. I voided both as "orphaned after the recompose". That is
erasing plays users could act on, for a reason that is ours and not the
pick's, and it is exactly the denominator loss 20261025a warns about ("the
only available remedy ... is to void it and lose the play from the
denominator entirely"). Both carried tier_source=fade_evidence:tracking,
i.e. they came from the market-structure path, and Andy independently named
Magic +5.5 as one of the night's good picks.
Houston -6.5 (3981655) is NOT in this class — it was voided for a coherence
failure, our own projection not covering the line we laid, which is a
property of the pick.
DECISION NEEDED: un-void 3981856 and 3981857 so they grade. The competing
concern is double counting, since Minnesota -7.5 replaced them in the same
slot and grading all three gives the card 5 NBA picks for 3 slots. The
honest answer is probably to grade all of them and record that the card was
recomposed, rather than delete the earlier version.

## R85 · THE SAME ORDERING BUG IN NBA, NHL AND MLB — 2026-10-30
NCAAF fixed this run: the externals pull, alignment/oddscrowd snapshot and
line_snapshot steps moved from ~400 lines BELOW the recompute to directly
above it. Audited the other four:
  NFL    ALREADY CORRECT — externals #52, alignment #55, line_snapshot #56,
         recompute #57. Nothing to do.
  NBA    BROKEN, and structurally harder. `Build NBA game context` is step
         493 and that is where scoring happens — there is no separate
         recompute step — while `ScoresAndOdds scrape` is 871, `Compute
         alignment` 910 and `Write line_snapshot` 965. The obstacle: the
         aligner WRITES oddscrowd_snapshot INTO nba_game_context, so the
         rows must already exist; and if the context builder upserts over
         them it may null the column it just received. Fixing this needs the
         context build split into "build rows" then "score", or the aligner
         proven non-destructive against a freshly built row.
  NHL    Same shape as NBA — verify before changing.
  MLB    Recompute #26, external aggregation #106. Broken, but off-season,
         so lowest urgency.
WHY NBA MATTERS MOST: NBA is the live sport where evidence families NEVER
disagree (0 of 56 units, measure_family_weight_leverage.py). Public money is
the one family that argues against the favourite, so NBA is precisely where
this ordering fix would create the disagreement that makes weighting mean
anything.

## R84 RESOLVED · UN-VOIDED, THEY WILL GRADE — 2026-10-30
Andy: "unvoid so they grade." Done and confirmed by readback:
  3981856  Orlando Magic +5.5        result VOID -> NULL  (top_8 rank 2)
  3981857  Golden State Warriors +3.5 result VOID -> NULL  (top_8 rank 3)
Both live-captured, both gradeable (side + line + game_id present), both
carrying tier_source=fade_evidence:tracking.
3981655 Houston Rockets -6.5 LEFT VOID on purpose — that one failed
coherence (our projection had Houston by 5.5 against a -6.5 line), which is
a property of the pick, not of our bookkeeping.
Tonight's Sweat Card therefore grades five NBA plays across three slots
(76ers ML, Detroit -8.5, Minnesota -7.5, Orlando +5.5, Golden State +3.5).
That is the honest denominator: the card really did show all five across the
evening. The reversal could not be stamped on the rows because audit is
immutable by trg_freeze_receipt_identity — R76 (no void_reason column) is
what makes that necessary, and it is now blocking an audit trail in both
directions.
NOT A DEFECT, checked and cleared: receipts carry surface-shaped `market`
values ('potd', 'dotd', 'parlay', 'chalk_parlay', 'game', 'fight'). I
briefly read the 76ers POTD's market='potd' with a null line as ungradeable.
It is not — market='potd' grades at 311 of 313 (99.4%). My test was naive,
the convention is deliberate.

## R86 · THE STEAM ROOM'S ONLY POSITIVE BUCKET IS OUT OF SEASON — 2026-10-30
Cut the Steam Room's live record down (audit_live_surface_roi.py
--surface sharp_card). 314 live graded bets, -7.07% overall, and the loss is
concentrated, not diffuse:
    ml   MLB     54  68.5% [56.1,80.9]  ROI +17.32%   <- the only edge
    rl   STRONG  69  42.0% [30.4,53.7]  ROI -18.38%   <- the leak
    rl   NFL     16  31.2%              ROI -40.72%
    rl   NHL     23  39.1% [19.2,59.1]  ROI -20.29%
    prop STRONG  41  46.3% [31.1,61.6]  ROI -20.01%
    ml   NCAAF   30  46.7% [28.8,64.5]  ROI -19.60%
    rl   NCAAF   50  48.0% [34.2,61.8]  ROI  -8.88%
    prop MLB     91  52.7% [42.5,63.0]  ROI  -9.44%
TWO CONCLUSIONS.
1. The single positive bucket is MLB moneylines, which is the edge already
   established independently (project_mlb_ml_is_the_one_real_edge: live
   63-29, 68.48%, +18.83%, n=92). MLB is out of season, so the Steam Room
   right now has NO positive bucket in its live record. Its 83 items over
   the last 14 days are all drawn from buckets that lose.
2. Confidence is an anti-signal on spreads: STRONG spreads -18.38% (n=69)
   against LEAN spreads -4.60% (n=14). Same shape as
   generate_sharp_card's own flip note ("our conviction on this market is an
   anti-signal") and project_nfl_curated_spread_is_the_leak (-45.9%).
HONEST LIMIT: rl STRONG's CI is [30.4,53.7], upper bound just above the
52.38 breakeven -- a credible negative, not a proven one. And MLB ml's lower
bound of 56.1 sits AT the bottom of the ML breakeven band, so even the good
bucket is marginal by the correct standard.
CANDIDATE ACTIONS, cheapest first: (a) stop publishing STRONG spreads on the
Steam Room -- it is the largest clearly-negative bucket; (b) ask whether the
surface should publish at all outside MLB until a bucket clears, which is a
product call not a code one; (c) note the Sweat Card (+4.59%, n=221) and the
Fade (+7.02%, n=74) are both positive, so the volume could move there.

## R87 · NCAAB CAN NEVER BE BETTABLE: TWO ID SCHEMES, TWO DATES — 2026-10-30
Andy: "The Steam Room needs to find a way to play nba, ncaab when online and
all other sports when live. THIS IS A MUST."
The Sharp took 0 of 3 bettable NCAAB picks. Its own dry-run says why:
"NCAAB no-market-price drops: 3 (ML pick with no book moneyline —
unbettable)". Every NCAAB game has close_spread/close_total/home_ml_close/
away_ml_close NULL while the engine still forms an ML opinion on it.
ROOT CAUSE, and it is not the season gate:
  ncaab_game_context  game_id = 32-char MD5 ('cf863e45a3252d...'),
                      game_date = 2026-10-30 for all 7 rows, prices NULL
  ncaab_game_results  game_id = slug ('ncaab_20261101_Villanova_Notre_Dame'),
                      game_date = 11-01..11-03, prices PRESENT
The same 7 matchups exist in both tables under incompatible id schemes AND
dates 2-4 days apart, so no join can carry the prices across. Same family as
R80 (line_snapshot vs nfl_game_results) and
project_ncaaf_game_id_is_built_from_team_names.
Also note ncaab_odds_pull.py writes to ncaab_game_RESULTS, not _context, and
its dry run today returns full prices for all 7 games (sp/tot/both MLs) — so
the DATA is fine and available right now. This is purely a wiring defect.
SEASON GATE IS A RED HERRING: season_gate SEASON_WINDOWS['NCAAB']=(11,4) so
is_sport_in_season flips true on 2026-11-01, but fixing the date will not
fix the join. NCAAB would still be priceless on 11-01.
SECOND DEFECT, flag for confirmation: ncaab_game_context dates all 7 games
2026-10-30 while the Odds API commence times put them 11-01..11-03. The
feed's commence_time is authoritative, so the context builder looks to be
stamping the run date rather than the tip date — the daily-default family
(project_align_status_daily_default_1029,
feedback_window_from_runner_clock_is_a_day_ahead). If so the games are also
on the wrong day in the app.
FIX: bridge the ids (bridge_football_game_ids.py is the existing pattern) or
have ncaab_odds_pull write context rows keyed the way the context builder
keys them. Then re-check the dates.

## R87 RESOLVED + R88 · NCAAB IS BETTABLE; A SIGN BUG WAS HIDING BEHIND IT — 2026-10-30
Andy: "The Steam Room needs to find a way to play nba, ncaab when online and
all other sports when live. THIS IS A MUST." Built to completion.
THREE defects, each of which alone made NCAAB unbettable:
 1. PRICES NEVER REACHED THE CONTEXT. ncaab_odds_pull wrote only
    ncaab_game_results under a slug id while the engine and the Sharp read
    ncaab_game_context keyed by the ODDS API EVENT ID. Verified the context
    game_id IS the event id, 7 of 7, so no name or date matching was needed.
    Added patch_context() to the puller: PATCH-only (never upsert, the
    context builder owns row creation), price columns only, open_* left
    untouched so the line-movement record survives, and both alias families
    written (close_home_ml for the Sharp, home_ml_close for the puller's own
    convention) because filling one was the whole defect.
 2. primary_play HAD NO `side`. All four return branches of
    ncaab_game_context.compute_primary_play carried type/tier/label/sub and
    no side, so generate_sharp_card computed side_ml=None and the no-price
    gate refused a pick whose price was on the row. Added `side` to all four
    (plus `line` on the totals), and backfilled the 6 stored picks by
    deriving the side from projected_spread exactly as the writer derives
    `fav`, then VALIDATING against the stored label -- 6 of 6 agreed.
 3. R88 · THE JUICE SWAP GUESSED THE SIGN CONVENTION. Fixing 1 and 2
    activated a path that had never been able to fire on NCAAB. The
    over-juiced-ML swap used `spread_line = -sp if HOME else sp` with a
    comment asserting close_spread is "away-perspective (positive = home
    favored)" -- the MLB/NFL convention. NCAAF, NBA and NCAAB store the
    OPPOSITE sign, so Iowa St. would have published as "+13.5" when the real
    line is -13.5, and Villanova as "+7.5" instead of -7.5. Now keyed on
    consistency_watchdog._CLOSE_HOME_POSITIVE, with NCAAB added on direct
    evidence (sign of close_spread agreed with the MONEYLINE about which
    side is favoured, 7 of 7), and a sport absent from the table is DROPPED
    rather than guessed.
RESULT, verified by composing the real rows: the Sharp goes 2 -> 5 items,
NCAAB 0 -> 3, all correctly signed and priced -- Gonzaga ML -221 (inside the
-300 cap), Iowa St. -13.5 at -110 and Villanova -7.5 at -110 (both swapped
off unplayable -1070 and -321 moneylines).
STILL OPEN: NCAAB should be promoted into the watchdog's own
_CLOSE_HOME_POSITIVE table so its checks cover the sport -- deliberately not
done here, that is a live CRITICAL detector and deserves its own review.
And the context dates all 7 games 10-30 while the feed says 11-01..11-03
(see R87 original) -- prices are keyed on the event id so the bridge is
unaffected, but the dates are still wrong somewhere.

## R89 · NBA AND NHL SCORE ONCE, EARLY, ON AN INCOMPLETE ROW — 2026-10-30
Andy: the Steam Room is "bleeding out by not getting enough selections" and
MUST play NBA. The Sharp takes almost no NBA because the ENGINE has almost
no NBA opinion: 86 of 107 games COVERAGE over 14 days, 17 bettable, and the
Sharp took 5 of those 17. Supply, not standards.
CAUSE: nba_game_context._apply_ensemble runs INSIDE `Build NBA game context`
(workflow step 493) and scores the row the builder holds in memory at that
moment. Everything that enriches the row lands afterwards — injuries (521),
Monte Carlo (551), the splits scrape (871), splits aggregator (880),
alignment/oddscrowd_snapshot (910), cover probability (947), line_snapshot
(965). NBA then NEVER re-scores. NCAAF (step 26) and NFL (step 57) both have
a "Recompute primary_play with enriched signals" step; NBA and NHL never got
one. Confirmed the builder does NOT write oddscrowd_snapshot, so an earlier
aligner would survive its upsert — but that alone would not help, because
the scorer reads the in-memory row, not the DB row.
MEASURED by re-scoring tonight's stored rows as they now stand:
  NBA  9 of 10 picks change — 5 promoted, 2 demoted, 2 lateral.
       Bettable would go 2 -> 5. Demotions include Detroit -7.5 and
       Houston -6.5, the latter being the pick whose receipt was voided
       tonight for failing its own coherence check.
  NHL  3 of 3 promoted — and ALL THREE are OVER totals, the same shape as
       NCAAB's five identical UNDERs: one calibration bias restated, in a
       market measured at 43.2% and unstable.
BUILT, AND DELIBERATELY AS A SHADOW: recompute_ensemble_primary_play.py
--sport NBA|NHL. One script, not a third copy of the 373-line NCAAF one — it
calls each sport's OWN _apply_ensemble so the season gate, defensive gates
and apply_to_pick come along and cannot drift from the build path. Honours
pick_lock per sport, preserves manual overrides, never erases a pick when
the recompute goes quiet, stamps updated_at (the leak guards compare it to
tip-off), and confirms writes by readback.
WHY SHADOW AND NOT LIVE: NBA has 0 of 32 enabled game signals with a
measured hit rate, so promoting these swaps one unmeasured opinion for
another. And the repo already ran this experiment — lr_v1 overwrote
primary_play on 457 picks and LOST on the judgeable flips, 42.0% against the
ensemble's 57.1% on n=50. Shadow goes to
jerry_cache.ensemble_recompute_shadow_{SPORT}_{date}.
NEXT: wire it into the NBA/NHL workflows in shadow mode so a graded record
accumulates, then promote only if it beats the live pick. NOT wired tonight
on purpose — both workflows have a timeout history (bumped 20/25 -> 45
minutes earlier today) and adding an untested scoring step to a
timeout-prone pipeline is how tonight's other defects were born.

## R90 · THE SCRIPT THAT RECORDS WHAT MAKES A PICK WAS NEVER WIRED IN — 2026-10-30
Andy, on tonight's NBA picks: "however we got to those picks we need to
assess as a baseline."
Captured the recipe. Only TWO NBA picks carried a bettable tier, and both
rest on the same two signals:
  Detroit -8.5 STRONG  conv 61 score 0.43  close 7.5  proj 10.8
      external:oddscrowd        w=0.34 n=46  AWAY  contrib 0.17
      nba_elo_away_ml_strong    w=0.26 n=20  AWAY  contrib 0.26
  Houston -6.5 STRONG  conv 67 score 0.55  close 6.5  proj  5.5
      external:oddscrowd        w=0.52 n=46  AWAY  contrib 0.26
      nba_elo_away_ml_strong    w=0.44 n=20  AWAY  contrib 0.25
      nba_offense_mismatch_away w=0.20 n=0   AWAY  contrib 0.11
Minnesota -7.5 and 76ers ML did NOT come from primary_play — the card
composed them (76ers is COVERAGE in primary_play, Minnesota's primary_play
is a total).
THE PROBLEM WITH CALLING IT A BASELINE TODAY: external:oddscrowd, the
heaviest weight on both picks, has NEVER been attributed on NBA. Its n=46 is
borrowed from NCAAF (39) + NFL (8), and in NCAAF it measures 46.2%
CI[30.5,61.8] — below a coin flip. nba_elo_away_ml_strong is NBA n=10 at
70.0% CI[41.6,98.4], promising and not quotable.
nba_offense_mismatch_away is n=0 carrying the unmeasured 0.20 floor.
CAUSE: attribute_ensemble_sources.py reads primary_play._ensemble_sources
and writes the real drivers to signal_attribution. It is wired into ZERO
workflows — grep of .github/workflows returns nothing. Its own docstring,
dated 2026-10-27, was written for Andy's standard and records the same table
of missing attribution (NBA 0, NHL 0). signal_attribution_snapshot.py, which
IS wired, only writes a hardcoded family list (OFF_RATING_GAP, LR_SHADOW,
SPLITS_SHARP, ANCHOR, GOAT, COHORT_*) and never touches _ensemble_sources.
RAN IT, all sports, --apply: 368 rows written, 0 failed. Attribution now:
  MLB 358 rows / 21 signals   NBA 107 / 18   NCAAF 3286 / 107
  NFL 1106 / 85               NHL 274 / 36
Both of tonight's drivers now carry rows with their SIDE recorded and
signal_was_right=None pending results, so tonight becomes the first graded
datapoint of a real baseline rather than an anecdote.
NEXT, and it is small: wire attribute_ensemble_sources.py into all five
sport workflows, AFTER primary_play is written. Right now tonight's run is a
one-off and tomorrow's picks will go unrecorded again.
ALSO LIVE TONIGHT — a natural A/B: the published picks came from the
early-row score, and R89's shadow holds what the completed row would say.
The shadow DEMOTES Detroit and Houston. If Detroit covers, the early read
beat the late read and R89 should not be promoted; if it does not, the
ordering fix has its first evidence. Either way it is measured, not argued.

## R91 · THE SAME SIDE WENT OUT AT TWO DIFFERENT NUMBERS — 2026-10-30
Andy, on tonight's dog spreads. Every receipt for Lakers @ Golden State:
    10-29 22:41  game_read   Los Angeles Lakers ATS      line=None
    10-30 13:51  fade        Golden State Warriors +1.5  -108
    10-30 21:53  sweat_card  Golden State Warriors +3.5  -115
Two surfaces on the same side two points apart, and a third on the OTHER
side with no number. Orlando @ Toronto has the milder form: both said Magic
+5.5, priced -105 and -110.
CAUSE: nobody mis-derived it. generate_the_fade derives its line from
ctx.close_spread (line 452, home_handicap = sgn * cs) and so does the card,
each at its own run time -- and close_spread is a LIVE odds column. 89% of
money came in on the Lakers and the spread walked 1.5 -> 3.5 over the eight
hours between the two publishes. Same column
project_nfl_stale_line_and_inverted_weights_1030 found freezing stale.
FIX, three parts:
 1. public_receipt.frozen_quote(sport, game_id, market, side) returns the
    line+price of the EARLIEST live receipt for that key -- what the first
    user actually saw. VOIDed receipts skipped. None means nothing is
    committed yet and the caller becomes the commitment. Same
    first-publisher-wins rule publish_lock uses for tier.
 2. receipt_invariants.check_line_coherence(), wired into
    public_receipt.capture(), WARNs when a receipt's number disagrees with
    one already published for the same game+market+side. One batched read.
    WARN not REFUSE deliberately: refusing the second receipt leaves a pick
    visible with NO receipt, which cannot be graded at all.
 3. generate_sweat_card consults frozen_quote before publishing and aligns
    line+odds to the commitment, printing every override.
VERIFIED on tonight's real collision: the invariant flags Warriors +3.5
against the fade's +1.5 with the surface and timestamp, and frozen_quote
returns {line 1.5, odds -108, surface fade} for that key and
{line 5.5, odds -105} for Orlando.
STILL TO WIRE: the Fade, Steam Room, Ledger and Degen composers. They are
not broken by this change -- the invariant now makes any of them that
disagrees visible at capture, which is how the remaining ones get found.
ALSO OPEN on this same game: game_read published the LAKERS while the fade
and card published GOLDEN STATE. That is R78's missing intent marker, not a
line problem.

### R92 — POTD receipts carry no game_id, so no game-keyed check can see them
`daily_best_bet_history` stores the matchup as a STRING (`"Marshall @ Old
Dominion"`) and has no game_id column, so all **334 potd receipts ever
written are unkeyed (0%)**. Consequences: `frozen_quote` and
`check_line_coherence` both key on game_id and are blind to the POTD;
`grade_game_market_receipts` cannot reach it either (hence the separate
`grade_potd.py` path). Today Marshall ML went out at **-145 (potd) and -142
(sweat_card/sharp_card)** — the same bet, and nothing could compare them.

DO NOT derive the id from the team names. That is the defect behind
`project_ncaaf_five_team_name_mismatch_1021` (21 duplicate game rows, one
with home/away inverted) and `project_nhl_stl_name_mismatch_1013`. The fix
belongs upstream: `jerry_anchor_potd` already reads a context row when it
chooses, so it should persist that row's game_id into
`daily_best_bet_history` (needs a column add), and `public_receipt`'s potd
builder passes it through. UFC is the other unkeyed surface (4 rows today) —
a fight has no game_id in this schema at all, which is a separate question.

### R93 — six UFC Sharp receipts still carry a decimal price in pick_odds
`_compose_ufc` is fixed (2026-10-31) but the six already-published rows still
hold `pick_odds` 1 or 2. `_repair_ufc_decimal_odds.py --apply` is written,
dry-run clean, and converts each from the decimal still printed in its own
label (1.375 -> -267 etc., matching the same fights' game_read prices). The
write was blocked by the sandbox classifier and needs Andy's go-ahead. Until
then those rows score a WIN as +0.01u and understate the Sharp's ROI.

### R94 — nba_game_context makes its OWN Odds API call instead of reading the priced copy
Today NBA was the only live sport with no market data: `nba_game_context`
held **0 of 7** rows with close_spread/close_total/any ML column, while NHL
(10/10) and NCAAF (47/47) were fine. The scrape was NOT the problem --
`nba_odds_pull.py` had already written all 7 games fully priced into
`nba_game_results`, and the odds pull deliberately "adopts the league's
canonical game id so results and context join". The context builder never
looks there: it issues a second, independent Odds API request and leaves
every price column NULL when that one comes back empty.

Consequence chain: no market -> primary_play has market/line None and
tier COVERAGE -> nothing clears `_is_any_tier` -> **zero NBA on the Sweat
Card and zero on The Sharp tonight**, which reads as a tier-gate problem
and is not one. Same shape as `project_align_status_daily_default_1029`
("Scrape was FINE").

Fix: when the live lookup yields no market for a game_id, fall back to
`nba_game_results` for that id. Re-running the builder by hand priced all
7 immediately, so this is a resilience gap, not a broken feed. Check NHL
and NCAAB builders for the same duplicated call.

### R95 — the lock's own log line reports pre-recovery labels
With the spread recovery wired in, the run prints
`recovered -7.5 ... label now 'Portland Trail Blazers -7.5'` and then
`[LOCK] keeping published 'Portland Trail Blazers ATS' (rebuild wanted
'Portland Trail Blazers -7.5')`. The stored value IS the recovered one --
`old_lbl`/`new_lbl` are captured before the recovery runs, so the message
is stale, not wrong. Cosmetic, but it reads as if the fix did not apply.

### R96 — a receipt cannot be repaired by PATCH, and the trigger is RIGHT
Both repair scripts were run. Both came back **HTTP 200 with zero rows
changed** on every row -- the silent-refusal shape, caught only because
each confirms by readback (feedback_204_is_not_a_write). The cause is
`freeze_receipt_identity` as redefined by migration 20260918b:

  * `pick_line` and `pick_label` are **FULLY frozen** -- `NEW := OLD`
    unconditionally. They cannot even be filled from NULL.
  * `pick_odds`, `tier`, `conviction`, `matchup`, `audit` are **set-once**:
    NULL -> value is allowed, value -> other value is reverted. The 6 UFC
    rows already hold `pick_odds` 1 or 2, so they are frozen.

Do NOT work around this. That immutability is what makes "we show the
receipts" mean anything, and bypassing it to tidy up our own bad write is
precisely the thing it exists to prevent.

**The real defect is the migration's premise.** Its comment reads
"Identity fields (player_name/prop_type/pick_line/etc.) still fully
frozen -- they cannot be null-filled because they're required at insert."
That is true of PROPS and false of game-market receipts: 12 receipts on
finished dates carry a line market with `pick_line` NULL, so the column is
plainly not required at insert on that path. `pick_line` was frozen under
an invariant it does not have.

Three legitimate remedies, in increasing order of commitment:
  1. **Capture-time** -- DONE (a9712469 + 33c41cc3). Future runs carry the
     line and the right price. Nothing retroactive.
  2. **void + replace** -- the path 20261025a deliberately enabled, whose
     own note says it exists so "a voided receipt could be replaced by the
     correct side". Legitimate here only while the game has not started,
     and distinct from the 10-30 void Andy reversed: that erased a play
     users could have acted on for OUR bookkeeping, whereas a lineless
     receipt is unactionable as written and the replacement is the SAME
     side and market with the number added. Needs Andy's call -- it touches
     the public record.
  3. **Migration** making `pick_line`/`pick_label` set-once rather than
     fully frozen, matching what `pick_odds`/`tier`/`conviction` already
     do in the same trigger. Principled -- NULL commits to nothing, so
     filling it is not mutating a commitment -- but it relaxes the
     receipts' immutability guarantee and is Andy's call. Must execute the
     migration, not just read it (feedback_execute_the_migration_not_just_read_it),
     and must not end on NOTIFY pgrst
     (feedback_notify_pgrst_breaks_andys_sql_client).

Scope note: tonight's 3 affected rows are COVERAGE-tier **game reads**, not
card plays -- no user was shown a play. The cost is 3 ungraded reads, same
as 10-30's four.
