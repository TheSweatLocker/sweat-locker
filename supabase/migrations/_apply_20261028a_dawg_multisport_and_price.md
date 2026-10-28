# 2026-10-28a — Dawg of the Day: all sports, and a price

**Paste `20261028a_dawg_multisport_and_price.sql` into the Supabase SQL editor
and Run once.** Idempotent (`IF NOT EXISTS` throughout), safe to re-run.

## Why this is needed

Andy, 2026-10-28: *"Wiring all sports into Dawg of the Day and ensuring that
if its plus odds it waulifies as Dawg so if engine like a -1.5 Puckline or
other spread thats pluss odds that should qualify as dawg as well right?"*

Three things block that, and all three are schema:

| | |
|---|---|
| `game_date` is the PRIMARY KEY | one row per calendar day for all sports combined — a multi-sport Dawg **cannot be stored** |
| there is no `sport` column | all 132 rows are MLB, and nothing downstream could tell them apart if they weren't |
| there is no PRICE column | Dawg is a plus-odds surface *by definition* and its 60-68-4 record has **never** been expressible in units; 0 of 132 dawg receipts carry `pick_odds` |

A 46.9% hit rate is profitable at +150 and losing at +110. We currently
cannot tell which this surface was. Same hole found on POTD today (69% of
its real picks have no price).

## The answer to the plus-odds question, measured

By price a plus-odds spread **is** a dog, so it qualifies. But the two
populations are opposites — every plus-odds pick we have ever published:

```
market       n     W    L    hit%     units    breakeven
ML          80    42   38   52.5%   +12.44u      46.7%
SPREAD/PL   21     6   15   28.6%    -7.94u      46.5%
  NHL spread alone  17   4-13   23.5%   -8.39u
```

A −1.5 puckline at +130 is not an underdog; it is a **favourite at a long
price**. So the rule is honoured and ordered: a plus-odds ML takes the slot
when one exists, a plus-odds spread takes it when none does, and `market` is
stored so the record splits rather than blending a 52.5% population with a
28.6% one.

## What is already shipped and waiting on this

| piece | state |
|---|---|
| `mlb_pipeline/generate_dawg_multisport.py` | written, dry-run verified, **refuses to run** until this migration lands (its preflight names this file) |
| `mlb_pipeline/generate_dawg_of_day.py` | untouched — the legacy MLB path still works |

Not yet wired into a workflow: that waits until the migration is applied and
one live day has been eyeballed.

## Backtest of the new selector

One pick per sport per day, from priced `jerry_reads` in the +100..+250 band:

```
all            n=38   20W-18L   52.6%   +7.27u   median +115, BE 46.5%
  ml half      n=29   16W-13L   55.2%   +7.09u
  rl half      n= 9    4W- 5L   44.4%   +0.18u
MLB 8-3   NBA 2-1   NFL 2-1   NHL 7-8   NCAAF 1-5
```

Thin — n=38, CI straddles — and stated as thin. The one-per-DAY variant
(single best candidate across all sports) measures better at **15-11, 57.7%,
+8.57u on n=26** and is available with `--one-per-day` if the surface should
stay singular. That is a product call, not a data one.

Two things deliberately NOT done:

* **no sport exclusion.** NCAAF is 1-5, which is six games. That is not
  grounds for banning a sport, and the per-sport record will settle it.
* **no no-play rule on spread-only days.** 24% of sport-day slots have only
  spread candidates, and those slots are 4-5 **+0.18u on n=9** — flat, not
  clearly losing, and far too thin to gate on. The `market` column exists so
  this can be revisited with evidence instead of a guess.

## Verify after running

```sql
select sport, market, count(*), count(price_american) as priced
from daily_dawg group by 1,2 order by 1,2;
-- expect: MLB / ml / 132 / 0   (history, honestly unpriced)

select indexname from pg_indexes
where tablename = 'daily_dawg' and indexname = 'daily_dawg_sport_date_uniq';
```

then

```
cd mlb_pipeline && python generate_dawg_multisport.py          # dry
cd mlb_pipeline && python generate_dawg_multisport.py --apply
```
