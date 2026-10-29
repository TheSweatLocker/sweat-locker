# 2026-10-29a — NBA gets a matchup axis

**The SQL file is now bare: 8 statements, zero comments, pure ASCII.** All
the rationale that used to live in its header is in this note instead,
because the comments are the prime suspect for two failed pastes (below).

```
python mlb_pipeline/validate_migration.py supabase/migrations/20261029a_nba_four_factor_matchup_signals.sql --parse-only
```

## HOW TO RUN IT — and how to bisect if it fails again

There is **no `BEGIN`/`COMMIT`**. Every statement is independently
idempotent (`ADD COLUMN IF NOT EXISTS`, `WHERE NOT EXISTS`), so a partial
run is safe and re-running costs nothing. That is deliberate: it means you
can **run the statements one at a time** and the error will name which one.

| # | statement | what it does |
|---|---|---|
| 1 | `ALTER TABLE public.nba_game_context ADD COLUMN IF NOT EXISTS …` | 14 nullable numeric columns |
| 2–7 | six `INSERT … SELECT … WHERE NOT EXISTS` | one matchup signal each |
| 8 | `NOTIFY pgrst, 'reload schema'` | PostgREST picks up the columns |

**If it fails again, run statement 1 alone.** If a bare `ALTER TABLE … ADD
COLUMN IF NOT EXISTS` also returns `42P01: relation "a" does not exist`,
then nothing in this file is the cause and the problem is in the client or
the session — tell me that result, it is the single most useful datapoint
left.

## Three failed pastes, and what each one ruled out

**Attempt 1 — `42P10: there is no unique or exclusion constraint matching
the ON CONFLICT specification`.** Real defect, mine. `ON CONFLICT
(signal_key)` named a key `signal_sources` does not have: the table declares
`UNIQUE (signal_key, sport, market_scope)`, and 20260820 added
`(signal_key, sport)` only **conditionally**. No conflict target is safe to
assume. Also proved the rollback was whole — all 14 columns still `42703`,
zero rows at the new origin — so nothing was ever half-applied.

**Attempt 2 — `42P01: relation "a" does not exist`.** I had repaired the
42P10 with `INSERT … SELECT … FROM (VALUES …) AS v(…)` and `::` casts, which
was a second unproven guess rather than a fix. Blamed the derived table.

**Attempt 3 — the same `42P01`, using the proven 20260911c shape.** This is
the informative one: it **cleared the construct of blame entirely.** Six
plain `INSERT … SELECT … WHERE NOT EXISTS` statements, the exact shape
20260911c uses and which is still live in this database (4 rows at origin
`SEEDED_QB_SPLITS_911`), failed identically.

### What that leaves

**There is no identifier `a` in the SQL code.** Verified by stripping every
string and comment, which leaves exactly three identifiers in the whole
INSERT: `AS`, `s`, `v`. Postgres cannot have produced `relation "a"` from
the code, so the client is parsing something that is not code.

The one thing attempts 2 and 3 share and attempt 1 did not is **the comment
block I added**, and attempt 3's comments literally contained `` `a` `` in
backticks plus a lot of non-ASCII (`·`, `—`, `══`). So this version removes
both variables at once:

```
bytes 6394   max byte value 125 (pure ASCII)
'--' present: no      '/*' present: no      backtick: no
CR: no       tab: no       double-quotes: 12 (the six side_expr values)
```

The `COMMENT ON COLUMN` statement was dropped too — it was documentation,
its prose is reproduced below, and it was one more string to mis-lex.

> `home_opp_efg_pct` is the eFG% that opponents of the HOME team shoot
> against it, i.e. how well it defends the shot. Paired with `away_efg_pct`
> to express a shooting mismatch.

## Why these signals exist

Andy, 2026-10-28, on the standard the engine should meet:

> *"Better offense, rushing exploit against bad rush defense, money flow
> split slight edge on sharp side, home team covers at home more often than
> away though, team is on hot streak. Based on all these and other signals I
> assess the eagle to cover tonight."*

Audited against those five categories, **NBA was the worst of the five
sports** — exist / fire / measured:

| category | MLB | NFL | NCAAF | **NBA** | NHL |
|---|---|---|---|---|---|
| 1 team quality | 10/2/0 | 4/4/0 | 16/11/0 | **0/0/0** | 0/0/0 |
| 2 MATCHUP exploit | 0/0/0 | 3/1/0 | 0/0/0 | **0/0/0** | 0/0/0 |
| 3 money flow / sharp | 5/0/1 | 3/0/0 | 3/2/0 | **0/0/0** | 0/0/0 |
| 4 situational home/away | 9/1/0 | 8/8/0 | 8/6/0 | **6/0/0** | 9/8/0 |
| 5 form / hot streak | 23/2/0 | 22/13/0 | 20/16/0 | **12/0/0** | 10/1/0 |

NBA is 0/0/0 on three categories and **nothing fires** in the two where
signals exist. Its 14 uncategorised signals are all Elo — the price
restated. That is why its read says *"Elo model has the Hawks at 78%"* while
NCAAF already says *"offense running +0.184 EPA/play vs a defense allowing
+0.039 — a 14.5-point efficiency chasm"*.

## The obvious fix was measured and rejected

`home_off_rating` vs `away_def_rating` looks like the NBA analogue. The
algebra kills it:

```
(ho + ad) - (ao + hd)   ==   (ho - hd) - (ao - ad)
"the matchup"                 the NET-RATING differential
```

The same expression, because off and def ratings are both
points-per-100-possessions and symmetric. Measured on 63 games with
point-in-time ratings:

```
corr with home margin           +0.341
corr with margin vs the SPREAD  +0.070    <- no edge
corr with the LINE ITSELF       +0.688    <- the market already has it
```

Seeding that would have added another price proxy — the mechanism behind the
favourite lock. Football differs: a bad team can have a good run game
against a bad run defence, so rush-vs-rush is a real second axis. Basketball
needs the four factors.

## What ships

Three complete four-factor pairs exist for the current season, all 30 teams:

```
efg_pct 0.461-0.646  /  opp_efg_pct 0.479-0.602     shooting
tov_pct 0.108-0.202  /  opp_tov_pct 0.106-0.204     ball security
ft_rate 0.155-0.308  /  opp_ft_rate 0.167-0.332     whistle
orb_pct 0.180-0.357  /  opp_orb_pct NULL on 30/30   <- NOT SEEDED
```

Six signals (three axes x two sides), `class='matchup'`, `market_scope='rl'`.
Thresholds are the **measured** p70 / p30 of the live 30-team distribution,
fixed — not re-fitted per run, because re-learning a threshold has cost this
repo 2-8 points every time (the fade 55.7->47.7, the wind cutoff
55.8->53.6).

```
efg_pct     p70 0.5423      opp_efg_pct p70 0.5538
tov_pct     p30 0.1320      opp_tov_pct p30 0.1303
ft_rate     p70 0.2371      opp_ft_rate p70 0.2512
```

**The rebounding exploit is deliberately NOT seeded.** `opp_orb_pct` is NULL
on all 30 rows, so the condition could never be true, and a
permanently-silent signal is worse than none — it reads as tested-and-quiet.
Fixing that scraper field is a separate item; the fourth signal lands with it.

Also fixed in `nba_game_context.enrich_team_stats`: `efg_pct` was already in
its select **and** in its FIELDS merge and was then never written to the row
— fetched and thrown away. This migration adds the 14 columns that write now
lands in.

## These are UNMEASURED and say so

`hit_rate_pct` and `sample_n` are NULL, so `edge_weight` gives them the
unmeasured floor and the 2026-09-03 credibility gate holds a LEAN resting
only on them at COVERAGE.

**They cannot be validated tonight.** `nba_team_stats` is a current-season
snapshot, so testing it against games already inside that snapshot is the
leak that produced the fake NCAAF 67.8% (z=+4.07) retracted on 09-26. They
are seeded to **fire and accrue attribution**, and the per-sport refit wired
on 10-28 will weight them against a direction-only control once n>=10. With
NBA's schedule that is roughly 1-2 weeks to n>=10, 3-5 weeks to n>=30.

## Verified before each handover

The conditions are replayed through `signal_expr` itself, read back out of
the SQL file rather than trusting the generator:

```
signal                        GOOD  NEAR  BLANK  stren  side
nba_shooting_exploit_home     True False  False  0.436  HOME_RL
nba_shooting_exploit_away     True False  False  0.436  AWAY_RL
nba_ball_security_home        True False  False  0.426  HOME_RL
nba_ball_security_away        True False  False  0.426  AWAY_RL
nba_foul_leverage_home        True False  False  0.340  HOME_RL
nba_foul_leverage_away        True False  False  0.340  AWAY_RL
swallowed-error report: EMPTY
```

`NEAR` is a ctx sitting just **under** each gate, so the thresholds are
proven to bite rather than merely to pass. `float` and `min` are
whitelisted; a blank ctx is False everywhere (the None-guard working, not a
swallowed NameError); `side_expr` resolves to `HOME_RL` / `AWAY_RL` matching
each key's suffix; prose renders.

Catalog claims checked through PostgREST:

```
all 12 inserted columns exist on signal_sources  -> 200
negative control (bogus_col_xyz)                 -> 400 42703
class / enabled types confirmed on a live matchup row
the 6 signal_keys already present                -> 0  (all 6 will insert)
20260911c's rows still live                      -> 4  (shape is proven)
```

## After running

```
python nba_game_context.py --days 2
   -> expect "four factors on N/N rows"
python ensemble_scorer.py --sport NBA --verbose
   -> expect nba_shooting_exploit_* / nba_ball_security_* /
      nba_foul_leverage_* in the contributions, class=matchup
```

## Still open for NBA after this

* `opp_orb_pct` NULL at source — blocks the rebounding exploit.
* Category 3 (money flow) is still 0 signals, though `align_status` is 85/85
  and `splits_summary` 66/85 populated. Money flow **inverts by sport**, so
  NBA's direction has to be measured before seeding, not assumed.
* Category 5 form signals exist but none fire; `20261021f` disabled the L10
  family for being direction-blind, which is the likely reason.
