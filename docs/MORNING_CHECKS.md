# MORNING CHECKS — the daily cadence

**Why this exists.** On 2026-11-06 Andy found six defects in the app that a
DB-only sweep had missed, including published spreads carrying moneyline
prices and `Ohio St. ML` on a board where no NCAAB favourite-ML price band
has ever cleared breakeven. The pattern behind nearly all of them: a step
passes green while writing nothing, or a correct fix exists and was never
scheduled. Three such fixes were found unscheduled in one night.

**The rule this doc enforces:** a number is not checked until it has been
checked ON THE SURFACE A USER SEES, not in the table behind it.

Run `python mlb_pipeline/morning_check.py` (to be built) for 1–6. Items
marked EYES require actually opening the app.

---

## TIER 1 — money is moving on these. Every morning, no exceptions.

### 1. Does every published pick carry a price that belongs to its market?
A spread / run line / puck line / total is a juice market, priced inside
±300. A moneyline is not. Anything outside that on a side or total pick was
priced against a different market and every ROI, breakeven and edge on it is
wrong.
- **Query:** `jerry_reads` where `call_market in (rl, spread, ats, puckline,
  total)` and `abs(price_american) > 300`.
- **Expected:** 0. On 11-06 it was 18, fifteen of them NCAAB.
- **Fix:** `price_picks.price_impossible()` now refuses to freeze these;
  clear and re-run `price_picks --sport X --write`.

### 2. Is any surface publishing a price past its own juice gate?
Documented gates: POTD max **−250**; resolver ML cap **−300**; Batter Hits
O0.5 never worse than −200; props band −300..+150.
- **Query:** published reads and card items by `price_american` band, per
  sport and market.
- **Expected:** nothing past −300 on an ML; nothing outside −300..+150 on a
  prop. On 11-06, five NCAAB ML reads sat at −2100 to −13333.

### 3. Do the reads and the card AGREE on each game?
The read is what a user opens; the card is what we publish. When
`jerry_reads.call_market` disagrees with `game_context.primary_play.type`,
one of the two is lying to a user.
- **Expected:** 0 disagreements on games that have not started. On 11-06 it
  was **26 of 32** NCAAB.

### 4. EYES — open one game card per live sport.
Check, for each: the spread sign matches the read (a margin is not a
spread), the money-flow panel has numbers, the situational/L10 records are
present, and the stats panel is populated for BOTH teams.
- This is the check that would have caught every item on Andy's 11-06 list.
  None of them were visible from the tables alone.

### 5. Did yesterday actually grade?
- **Query:** receipts for yesterday by surface — ungraded count, and
  `pick_odds IS NULL` count among graded rows.
- **Expected:** ungraded → 0 by late morning; unpriced-but-graded → 0. On
  11-06 game_read held 600 graded rows with no price.
- Also: the card's own result count must equal its receipt count.

### 6. Null sweep on the live surfaces.
**Andy's rule: if something is null it is probably broken.** It held on 6 of
6 instances on 11-06.
- **Run:** `mlb_pipeline/_null_audit.py`.
- **Expected:** no NEW column at 0% fill versus the baseline in
  `docs/TRIAGE_2026_11_06.md`. Investigate the WRITER, not the null.

---

## TIER 2 — freshness. Every morning, fast.

### 7. Feed recency per sport, not lifetime.
A sport can be dark a week and still look healthy in a lifetime count.
Check rows **per sport per day** for the last 7 days:
- `external_picks` — on 11-06 NBA went 131 → 2 → 0 over three days.
- `public_splits_archive` — NBA has **zero rows ever**; cz died 11-04; oc
  runs 74–987/day out of 2–3.4k and is the only feed whose `pick_side`
  indexes the public.
- `*_pipeline_props` — props land ~14:50–15:10 ET on game day; empty before
  that is normal, empty after is not.
- Ratings/roster tables: `roster_physicality` (NCAAB had **zero rows ever**
  until 11-06), `ncaab_rating_snapshots`, `*_team_stats`.

### 8. Did every scheduled step actually WRITE?
311 steps across 20 workflows and nothing asserts output moved. Until that
exists, check the output of the steps that have gone dark before:
Dawg (`daily_dawg`), The Sharp (`jerry_cache sharp_card_*`), POTD
(`daily_best_bet_history`), NCAAB pricing, the NHL prop bridge
(`ctx_game_id`).

---

## TIER 3 — weekly, not daily.

9. **Per-sport engine record** — reads, props by tier, and each published
   surface, with n and ROI at the rows' own prices. Anything whose published
   tier ordering is INVERTED is a scorer problem, not variance (NFL props:
   PRIME −5.15pp is its worst tier).
10. **Learning loop** — attribution rows and keys at n≥30 per sport. NCAAB
    was at **0 / 0**. A sport with no attribution is not improving no matter
    what else ships.
11. **Signal conditions** — `audit_signal_conditions_all_sports.py` per
    sport. Anything reading an absent or all-null column is dead code
    wearing an enabled flag.
12. **CLV** — currently impossible: `close_price_american` is 0% on every
    read in every sport and `close_*` is 0% on all props.

---

## WHAT I STILL NEED FROM ANDY

1. **Which surfaces count as "published"** for Tier 1/2 — just Sweat Card,
   POTD and The Sharp, or do game reads and the fade count as published too?
   It changes what a Tier 1 failure means.
2. **Thresholds you want alerted vs logged.** My instinct: Tier 1 any
   non-zero is an alert; Tier 2 a sport at zero for 2 consecutive days is an
   alert.
3. **Where you want to see it** — terminal on session start, a committed
   markdown file, or pushed to a dashboard in the app.
4. **Is there anything you check by eye today that is not in Tier 1 item 4?**
   That list should be yours, not mine — I have never looked at the app.
