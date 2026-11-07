# MORNING CHECKS — the daily cadence

**Why this exists.** On 2026-11-06 Andy found six defects in the app that a
DB-only sweep had missed, including published spreads carrying moneyline
prices, `Ohio St. ML` on a board where no NCAAB favourite-ML price band has
ever cleared breakeven, a game showing Purdue +21 against a read of Purdue
−19.5, and money flow absent for whole sports. The pattern behind nearly all
of them: a step passes green while writing nothing, or a correct fix exists
and was never scheduled — three such fixes were found unscheduled in one
night.

**The rule this doc enforces:** a number is not checked until it has been
checked ON THE SURFACE A USER SEES. Every item on Andy's 11-06 list was
invisible from the tables.

---

# TIER 0 — THE IN-APP SWEEP. This is the cadence. Do it first, every day.

Andy, 11-06: *"I essentially need a cadence of checking everything in app
every morning to include Sweat Card, Steam Room as a whole, and all data in
game cards"* — and *"spot check every sport, at least 2 games for that day,
to ensure data is populated and the engine is making sense in picking, i.e.
no −20000, no absolute blasphemy calls."*

## 0A. Sweat Card — the whole card
- [ ] Every pick shown has a **price**, and the price belongs to its market
      (a spread/total/puckline inside ±300; an ML never past −250 on POTD)
- [ ] Count of picks shown == count of picks with a receipt == what the
      CARD RESULT line claims. On 10-30 these were 5 / 14 / 5.
- [ ] No pick is a COVERAGE-tier floor fill presented like a real play
- [ ] Yesterday's recap record matches the receipts, and nothing sits
      Pending on a game that finished
- [ ] POTD: present or an explicit no-play with a reason — never blank

## 0B. Steam Room / The Sharp — the whole slate
- [ ] Item count is sane vs the target (it has run 0–7 against a target of
      15 for twelve straight days; **0 items on 11-01 and 11-03**)
- [ ] Every item priced, and no duplicate of a Sweat Card pick
- [ ] The record shown is the Steam Room slate, not the card's

## 0C. TWO GAME CARDS PER LIVE SPORT — NBA, NHL, NFL, NCAAF, NCAAB
For each of the two games, per sport:

**Data populated**
- [ ] Both teams' stats panel has numbers — **both**, not just one. Multiple
      CBB teams were blank on 11-06
- [ ] Money flow panel has numbers. **NBA has zero splits rows ever**;
      NCAAB runs 0–900/day; NHL is an order of magnitude under NCAAF
- [ ] Situational / L10 records present (NCAAB `away_l10` and `home_l10` are
      0% populated)
- [ ] Spread and total are shown and non-null (NCAAB posts a close_spread on
      only ~61% of its board)
- [ ] Externals / handicapper picks present (NBA externals went 131 → 2 → 0
      across 11-04..11-07)

**The engine is making sense** — this is the blasphemy check
- [ ] **No price past −300 on a moneyline.** On 11-06 the board carried
      Wisconsin ML −10000, Texas A&M ML −13333, Utah St. ML −20000
- [ ] The spread SIGN matches the read. A margin is not a spread — "Purdue
      +21" next to a read of "Purdue −19.5" means one surface is telling a
      user to back the wrong team
- [ ] The badge and the prose describe the SAME bet. Total reasoning under a
      moneyline badge happened on 11 games on 11-06
- [ ] Conviction is a real number, not 0 or a default, on anything
      publishable
- [ ] The pick is not on a market the books do not carry

## 0D. Receipts / POTD calendar
- [ ] Yesterday graded, and nothing graded-but-unpriced (game_read held 600
      such rows on 11-06)
- [ ] The POTD calendar shows one entry per day with a result, and the
      lifetime record moves. History is clean at 86-69 / 55.48%; the
      receipts ledger is ~2x inflated by VOID rows

**If anything in Tier 0 fails, that is the day's work. Stop and fix it.**

---

# TIER 1 — automatable, run alongside Tier 0

`python mlb_pipeline/morning_check.py` (to be built) should assert all of
these and exit non-zero on any failure.

1. **Impossible prices.** `jerry_reads` where `call_market in (rl, spread,
   ats, puckline, total)` and `abs(price_american) > 300`. Expected 0; was
   18 on 11-06. `price_picks.price_impossible()` now refuses to freeze them.
2. **Juice gates.** Nothing past −300 on an ML, nothing outside −300..+150
   on a prop, POTD never past −250.
3. **Read vs card agreement.** `jerry_reads.call_market` vs
   `primary_play.type` on unstarted games. Expected 0 disagreements; was
   **26 of 32** NCAAB on 11-06.
4. **Grading.** Yesterday's receipts: 0 ungraded by late morning, 0
   graded-but-unpriced, and card result count == receipt count.
5. **Null sweep.** `mlb_pipeline/_null_audit.py`. No NEW column at 0% fill
   versus `docs/TRIAGE_2026_11_06.md`. **Andy's rule: a null is probably
   broken** — it held on 6 of 6 instances on 11-06. Investigate the WRITER.
6. **Feed recency per sport per day, last 7 days** — not lifetime, which
   hid the NBA externals collapse entirely. `external_picks`,
   `public_splits_archive`, `*_pipeline_props`, `roster_physicality`,
   `ncaab_rating_snapshots`, `*_team_stats`.
7. **Did the steps that have gone dark before actually write?**
   `daily_dawg`, `jerry_cache sharp_card_*`, `daily_best_bet_history`,
   NCAAB prices, NHL prop `ctx_game_id`.

---

# TIER 2 — weekly

8. **Per-sport engine record** — reads, props by tier, each published
   surface, with n and ROI at the rows' own prices. A published tier
   ordering that is INVERTED is a scorer problem, not variance (NFL props:
   PRIME −5.15pp is its worst tier).
9. **Learning loop** — attribution rows and keys at n≥30 per sport. NCAAB
   was 0 / 0. A sport with no attribution is not improving.
10. **Signal conditions** — `audit_signal_conditions_all_sports.py` per
    sport. Anything reading an absent or all-null column is dead code with
    an enabled flag.
11. **CLV** — currently impossible: `close_price_american` is 0% on every
    read in every sport, `close_*` 0% on all props.

---

## OPEN QUESTIONS FOR ANDY

1. **Which two games per sport** — highest conviction, or the two we
   actually published on? My instinct: one published, one random, so a
   blank card cannot hide behind a good one.
2. **Alert vs log.** My instinct: any Tier 0 or Tier 1 failure alerts; Tier
   2 logs.
3. **Where delivered** — terminal at session start, a committed file, or a
   dashboard in the app.
4. **Anything you check by eye that is not in Tier 0?** That list should be
   yours — I have never opened the app, which is exactly why I missed all
   six of your 11-06 items.
