# Apply 20261029e — NHL regression to process (the anti-chalk signal)

Two `INSERT ... SELECT ... WHERE NOT EXISTS`. No NOTIFY, pure ASCII,
idempotent, no conflict target so a 42P10 is impossible. Safe to run twice.

## What it is

Andy, repeatedly: *"regression NEEDS TO BE ASSESSED"* and *"picking only
favourites is not data driven."* This is that, measured.

A team whose **results lag its underlying process** is the thing the market
underprices. Two gaps per team, both as z-scores because the inputs are on
different scales (L10 goals/game mean 2.92 sd 0.65 vs xGF/60 mean 2.50 sd
0.44 — a raw subtraction would have been the same-measure error):

```
off_gap = z(L10 goals for)     - z(xGF/60)    + = scoring above process
def_gap = z(L10 goals against) - z(xGA/60)    + = conceding above process
regression_score = -off_gap + def_gap          higher = due to IMPROVE
```

The side with the higher score is backed. Fires at a two-sided edge of 1.97.

## The measurement, priced and leak-free

Rows only count where `team_tendencies_updated_at` is on or before the game
date, so no post-game refresh leaks in. Graded on closing moneylines:

```
                                    n    hit     BE     edge      ROI   dogs
  CONTROL always the favourite    195  61.0%  60.4%   +0.58pp   +0.09%    0%
  regression, all games           195  55.4%  53.1%   +2.28pp   +2.82%   43%
  regression, |edge| >= 1.97       70  61.4%  54.9%   +6.55pp  +10.37%   37%
  regression, |edge| >= 2.93       36  72.2%  55.5%  +16.71pp  +32.07%   33%
```

Split-half at the seeded threshold holds: **+8.44pp then +4.66pp**.

**It beats the favourite control, and 37% of its picks are dogs** where the
control is 0%. That is the point: it is two-sided by construction, because
it asks who is due to improve rather than who is better.

**Not seeded for the puckline.** Positive at every threshold there
(+3.01 to +6.07pp) but it FAILS split-half (+6.08pp then -0.69pp), so only
`market_scope='ml'` ships.

## Replayed before seeding

Against 301 live `nhl_game_context` rows: **53 HOME fires, 40 AWAY, 0 eval
errors**, never both on one game, strength spans 0.301-0.600, and the replay
reproduced the analysis (n=71, 62.0%, +7.01pp, ROI +11.11%, 37% dogs). A
column check alone would have passed a condition that could never fire, so
the replay is the test that counts.

## Known limit

The means and sds are constants measured over 500 team-games. They need a
refresh as the season grows — logged as R38. The honest version of this
precomputes the gap into `nhl_game_context` as a column; that is the next
step, not a blocker.
