# 2026-11-16 — Fixing the signal calibration loop (NBA, NCAAB, NFL)

Andy: "Lets get to fixing then." This is recommendation #1 from
`2026-11-16-engine-audit.md` — stop scoring game signals on a basis that
rewards favourite bias — plus three defects found while implementing it.

All four live in `backfill_signal_tiers.py`, which runs nightly from
`mlb_pipeline.yml:1484` as `--sport ALL` and upserts `signal_registry` on
`(signal_name, sport, market_scope)`.

---

## What was broken

### 1. `SPORT_TABLES` had no `'NBA'` key

```python
ctx_table, res_table = SPORT_TABLES.get(sport, SPORT_TABLES['MLB'])
```

An absent key was not an error, it was a **silent substitution**. `--sport
NBA` replayed 67 NBA signals against `mlb_game_context`, matched nothing,
and fell through to the "no games" branch. [[feedback_absent_key_makes_a_gate_dead]]

### 2. NCAAB and NFL join across two different game_id spaces

```
ncaab_game_context.game_id  '089fe17e00a51999acb43407f067f273'
ncaab_game_results.game_id  'ncaab_20270307_Arizona_Houston'
nfl_game_context.game_id    '8c16104c8afc72b583531508b2aa1c33'
nfl_game_results.game_id    '20270110_TB_NO'
```

An id join returned **0 of 644** for NCAAB and **0 of 133** for NFL.
[[feedback_zero_rows_is_a_wiring_error]],
[[feedback_join_on_resolved_names_not_a_composed_key]]

### 3. "No games" had two causes and the branch wrote either way

The zero-games branch seeds every signal `UNVALIDATED, weight 0.3`. It was
written for preseason. It cannot tell a genuine off-day from a join that
matched nothing — and NBA, NCAAB and NFL all arrived there **with a full
slate of graded games**, so all three had every signal overwritten with a
prior. Worse, this writer runs **after** `refit_signal_registry` in the same
pipeline (step 1484 vs 710) and upserts the same key, so it was erasing what
refit had computed correctly minutes earlier.

Measured before the fix: NBA **73 of 88 UNVALIDATED** with 3 VALIDATED;
NCAAB **47 of 72 UNVALIDATED** with 2 VALIDATED and 59 rows last computed
11-04. That was this bug, not the evidence.

### 4. `be_frac` was never passed, so a moneyline was judged at -110

`_tier_for(n, hit_rate_frac, blind_frac, be_frac)` has accepted a
price-aware breakeven since 2026-10-29, and `refit_signal_registry.py:1096`
passes one. **This caller never did** — it called
`_tier_for(n_dec, hit_rate/100.0, blind)`, so the juice bar fell back to a
flat `0.5238` on every market, which is ~6 points light on a moneyline.
[[feedback_flat_110_inflates_every_ml_figure]]

---

## What changed

- `'NBA'` added to `SPORT_TABLES`; an unregistered sport now **raises**
  instead of silently replaying another sport's tables.
- `NAME_JOIN_SPORTS = {'NCAAB', 'NFL'}` plus an automatic fallback whenever
  an id join covers <20% of context rows: bridge on normalised
  `(away_team, home_team)` and a game_date within **±1 day only** — a
  results row two days out is a different meeting of the same two teams.
- The results select now also pulls `game_date, home_team, away_team,
  close_home_ml, close_away_ml`, and the price is carried onto the context
  row so the breakeven can come from the price the side actually took.
- Per-firing ML breakeven accumulated and passed as `be_frac`, with the same
  80%-coverage bar refit uses and a measured per-market fallback
  (`ml 0.5833`, `rl/total 0.5238`).
- **The seed branch now refuses to write** when the context table has rows
  in the window but the join produced none, printing the counts and the
  join strategy. A prior is a statement about absent evidence; writing one
  over evidence we failed to *read* is a lie about the evidence.
- `LAST_FETCH` diagnostics + a per-run `join[strategy]: matched/total` line
  so a future regression is visible in the log instead of silent.

## Result — the join, before and after

| sport | before | after | strategy |
|---|---|---|---|
| NBA | **0** of 264 | **253** of 264 | game_id |
| NCAAB | **0** of 644 | **505** of 644 | name+date |
| NFL | **0** of 133 | **129** of 133 | name+date |
| NHL | 376 | 376 | game_id (unchanged) |
| NCAAF | 565 | 565 | game_id (unchanged) |

**224 `signal_registry` rows now carry a measured tier** where three sports
previously carried a seeded prior:

| sport | measured hit_rate | VALID | DISC | ANTI | UNVAL |
|---|---|---|---|---|---|
| NBA | 65 of 88 | 6 | 6 | 12 | 62 |
| NCAAB | 50 of 72 | 9 | 6 | 0 | 54 |
| NFL | 366 of 416 | 23 | 50 | 97 | 238 |

The `be_frac` fix bites exactly where predicted — **`ncaab_projected_spread`
went from VALIDATED to UNVALIDATED** (n=403, hit 83.9%, ml scope): an 83.9%
moneyline hit rate does not clear the breakeven of the prices it fired at.

## ⚠ Two things this surfaced that are NOT fixed

### A. The price bar gates, but the EDGE is still measured against the side

In `_tier_for`, `be_frac` is a pass/fail gate (`if hit_rate_frac < _juice:
return UNVALIDATED`) while the `edge` that decides the tier is
`hit_rate - blind_frac`. So a signal can clear the juice by a hair and still
be VALIDATED on a large "edge" over a blind bet:

```
OFF_RATING_GAP  NCAAB  ml  n=219  hit=78.08  base=58.57  edge=+19.52  VALIDATED  w=1.00
```

At n=219 the 2SE bar is 6.76pp. If this signal's firings average about -250
(breakeven 71.4%) its true edge over the price is ~6.7pp — *under* its own
bar. Measured on ATS the same input is 49.2%.

`_tier_for` is the **shared canonical function for every sport** (MLB 701
rows, NFL 416, NCAAF 195…). Changing the edge basis from the side baseline
to the price breakeven would reorder every sport's registry and every
ensemble weight. **Not touched — that is Andy's call.**

### B. Two NFL spread signals are inverted, and auto-fade will weaponise them

```
nfl_home_spread_edge      rl  n=38  hit=15.8%  edge=-33.1  ANTI_VALIDATED
nfl_away_spread_edge_v3   rl  n=45  hit=17.8%  edge=-33.3  ANTI_VALIDATED
```

1-in-6 against the spread is not a fade, it is **a side convention that is
backwards, or a leak** — [[feedback_magnitude_cannot_express_a_side]],
[[feedback_edge_formula_sign_audit]]. And `ensemble_scorer.py:2278`
auto-flips any ANTI_VALIDATED signal with `n >= 25 and hr <= 0.47`,
**promoting it to VALIDATED so it earns real weight**. That turns these into
~84% ATS signals at full weight. An 84% ATS signal does not exist.

These rows were already in this state before today (they come from
`refit_signal_registry`, which could already read NFL) — this audit did not
create them, but they should not ship. The mirror pairs are safe: the
`_fade`-suffix guard at `ensemble_scorer.py:2276` already blocks a
fade-of-a-fade, so `h2h_home_dominant` (70.2% VALIDATED) and
`h2h_home_dominant_fade` (29.8% ANTI) do not double-count.

### C. One NBA signal can never fire

```
nba_prop_minutes_low_fade — signal_expr NameError: name 'p' is not defined
  expr: p.get('projected_minutes') is not None and ...
```

The expression references `p` in a game-context scope. The replay already
detects and prints this ("can never be true on ANY row"); the signal has
been dead since it was written. [[feedback_swallowed_nameerror_is_a_false_condition]]

## What changes tonight

`mlb_pipeline.yml --sport ALL` will now write measured tiers for NBA, NCAAB
and NFL instead of seeding priors. Five NBA signals newly qualify for
auto-fade (`n>=25, hr<=0.47`) and will begin contributing the opposite side:
`h2h_over_after_unders` 41.3% (n=46), `home_team_over_trend_season` 33.3%
(n=39), `away_team_over_trend_season` 42.4% (n=33),
`both_teams_under_trend_season` 45.0% (n=40), `nba_elo_over_edge` 42.6%
(n=47) — four of five are OVER signals flipping to UNDER.

This is the documented mechanism operating on evidence it could not
previously read, not a new policy. Flagging it because it moves live weights.
