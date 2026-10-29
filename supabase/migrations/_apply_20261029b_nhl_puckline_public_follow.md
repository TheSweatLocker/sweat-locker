# 2026-10-29b — NHL gets a measured money-flow signal

**Paste `20261029b_nhl_puckline_public_follow.sql` and Run once.** Two
`INSERT … SELECT … WHERE NOT EXISTS` statements, bare ASCII, no comments and
**no `NOTIFY`** — the three things that cost four pastes on 20261029a.

## Why

Andy, 2026-10-29: *"lets fix money flow problem and square away nhl … Fix
what you need for NHL to be equal with other sports."*

NHL had **zero** money-flow signals. Its category audit read 0/0/0, and on
tonight's Carolina game `align_status` said `no_money_data` on all three
markets while `splits_summary` was null — so nothing money-related could
reach a pick even in principle.

## The chain was broken in three places, all now fixed

```
sbd_splits_scraper.py --league nhl   -> 140 rows, 13 of 19 games matched
splits_v2_pipeline.py --sport NHL    -> 549 upserted, splits_summary 11/11
compute_align_status_nhl.py          -> align_status 11/11 games
```

The scraper works (ran it by hand, exit 0). It had not written since
**2026-10-28T20:38** despite the NHL pipeline completing at 15:22 today with
zero step failures — it needs chromium and sits under
`continue-on-error: true`. The `stderr_tail` capture added to
`run_step.sh` today will name the cause on the next run.

## The direction is FOLLOW, and that is measured, not assumed

Money flow **inverts by sport** (MLB fade, NCAAF follow), so NHL's direction
had to be measured before anything could be seeded. Taken over every NHL
game with a stored `spread_result`, using the LAST split snapshot before each
game and priced at the real puckline price:

```
NHL PUCKLINE
public>=55%  n=149  FOLLOW 62.42% vs BE 60.88%   +3.64%   FADE -13.54%
public>=60%  n=118  FOLLOW 66.10% vs BE 62.40%   +6.77%   FADE -20.15%
public>=65%  n= 96  FOLLOW 70.83% vs BE 62.95%  +14.52%   FADE -29.61%

NHL MONEYLINE (for contrast — barely anything)
public>=65%  n=161  FOLLOW 62.73% vs BE 61.75%   +0.52%
public>=75%  n=129  FOLLOW 65.89% vs BE 63.12%   +3.80%
```

Monotone in the threshold, and **fading is heavily negative** — so the Fade's
logic must never be pointed at NHL.

**Split-half holds** at the threshold being seeded:

```
public>=60%  n=118   EARLY 2026-10-01..10-15  38-21  64.41%  ROI +8.04%
                     LATE  2026-10-15..10-28  40-19  67.80%  ROI +5.49%
```

60% is seeded rather than 65% deliberately: 65% looks better in aggregate
(+14.52%) but decays across the halves (+23.03% → +6.00%), and re-learning a
threshold has cost this repo 2-8 points every time it has been measured.

## It is not a favourite machine, which is the point

At `>=60%` the public's heavy puckline side is **69 AWAY / 49 HOME**. On
tonight's board the rule fires on **6 of 11 games — 4 away, 2 home**:

```
Vancouver @ San Jose    A 65.0   fires AWAY
Chicago   @ Detroit     A 73.5   fires AWAY
Boston    @ St. Louis   A 69.5   fires AWAY
Montreal  @ Dallas      A 80.5   fires AWAY
Columbus  @ Carolina    H 66.5   fires HOME
Buffalo   @ Anaheim     H 89.0   fires HOME
```

And it is an **`rl`-scoped** signal, which is the half of NHL that has been
starved: ML carries 2.15x the chips of RL (73 vs 34 over 34 games), which is
why the cross-market comparison kept handing the board to favourite
moneylines.

## Seeded MEASURED, not at the floor

`hit_rate_pct = 66.1`, `sample_n = 118` are written with the rows, so
`edge_weight` prices them on evidence instead of handing them the unmeasured
0.20 floor. The nightly refit will re-measure them against their own
direction-only control once they accrue attribution.

**The honest limit:** the window is 2026-10-01 to 10-28 — one month, because
that is all the NHL `rl` split history that exists. It holds split-half
inside that month; it has not been tested across seasons.

## Verified before handover

Conditions replayed through `signal_expr` against **tonight's 11 real
`nhl_game_context` rows**, not a synthetic ctx:

```
nhl_puckline_public_follow_home   fires 2/11   blank ctx False   HOME_RL   strengths 0.365, 0.500
nhl_puckline_public_follow_away   fires 4/11   blank ctx False   AWAY_RL   strengths 0.350-0.500
swallowed-error report: EMPTY
```

Nested dict access was confirmed supported by `signal_expr` first
(`ctx.splits_summary["rl"]["HOME"]["bets_pct_avg"]`), and the expressions use
**double quotes** inside the Python so nothing terminates the SQL literal —
the first generated version used `'rl'` and broke the string. Quote count in
the file is even, SQL parses clean under sqlglot.

## After running

```
python nhl_game_context.py          # expect nhl_puckline_public_follow_* in contributions
python audit_read_pick_consistency.py --sport NHL --days 14
```

Tonight's NHL picks are publish-locked, so this changes tomorrow's board, not
tonight's.
