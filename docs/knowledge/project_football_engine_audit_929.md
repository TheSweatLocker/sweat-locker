---
name: project_football_engine_audit_929
description: "🚨 Football engine audit thru NFL wk3 / NCAAF wk4. Conviction is INVERTED (80+ = 46.7%, 50-59 = 64.7% on sides, n=179). Football totals 33.3% (n=48, -17.5u) — fading them goes 32-16. Sides +30.4u."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-29T15:27:29.508Z
---

Measured 2026-09-29 on 367 graded football picks (NFL weeks 1-3, NCAAF through
week 4). Andy asked for a full engine assessment before Thursday reads.

## Headline: two independent, well-sampled failures

### 1. CONVICTION IS ANTI-PREDICTIVE AT THE TOP — sides only, totals excluded

| conviction | W-L | hit% | vs 52.4% breakeven | n |
|---|---|---|---|---|
| 50-59 | 77-42 | **64.7%** | +12.3pp | 119 |
| 60-69 | 35-33 | 51.5% | -0.9pp | 68 |
| 70-79 | 42-28 | 60.0% | +7.6pp | 70 |
| **80-100** | **28-32** | **46.7%** | **-5.7pp** | **60** |

An 18pp spread in the WRONG direction between the lowest and highest buckets.
Verified NOT a totals artifact: the 80+ bucket is 48 rl / 12 ml / only 3 total.

Tier labels inherit the inversion — NCAAF PRIME 8-11 (42.1%), NFL PRIME 7-5
(58.3%) against NFL LEAN 9-2 (81.8%).

**The engine's confidence is currently worse than useless — it is a mild
contrarian indicator.** Anything that sizes stakes off conviction (Sharp Card
units, POTD selection, tier gates) is sizing backwards.

### 2. FOOTBALL TOTALS ARE A -17.5 UNIT LEAK

| | W-L | hit% | n |
|---|---|---|---|
| All football totals | 16-32 | **33.3%** | 48 |
| NCAAF | 14-29 | 32.6% | 43 |
| NFL | 2-3 | 40.0% | 5 |

Bad at EVERY conviction level: 31.0% (n=29), 37.5% (n=16), 33.3% (n=3). And bad
in BOTH directions — NCAAF OVER 4-14 (22.2%), UNDER 10-15 (40.0%) — so it is not
a directional bias to correct. It is anti-signal.

**Fading every football total pick would have gone 32-16 (66.7%).**

## What IS working

- **Sides: 182-135 (57.4%), n=317, +30.4 units.** The core side engine is sound.
- Net football is +12.9u, so totals are eating 58% of the sides' profit.
- NFL by week: wk1 11-5 (68.8%), wk2 9-7 (56.2%), wk3 9-6 (60.0%) — 29-18 (61.7%)
- NFL ML 17-8 (68.0%); NCAAF rl 119-94-2 (55.9%), ml 36-26 (58.1%)

## NCAAF dog bias — CONFIRMED STILL LIVE

favourites 120-79-1 (**60.3%**, n=200) vs underdogs 35-41-1 (**46.1%**, n=77).
Less extreme than the 9/26 measurement (24.3% vs 57.9%) but same direction and
now on a large sample. NFL is the opposite — dogs 21-11 (65.6%), favs 6-4
(60.0%) — so this is NCAAF-specific, not a shared engine flaw.

## Infrastructure problems found in the same pass

- **`week` is NULL on 241/242 NFL rows and ALL 478 NCAAF result rows.** Any
  week-based grouping is impossible from the results tables; weeks here were
  derived from game_date against the 2026-09-09 anchor.
- **`nfl_game_context.game_id` and `nfl_game_results.game_id` do not intersect
  AT ALL** (`20260927_BAL_DAL` vs Odds-API hex). Confirms
  [[project_nfl_game_id_mismatch_911]]. This is why `compute_surface_records`
  reports only 35 graded nfl_sides — the graders cannot join. All numbers above
  required a (game_date, away_team, home_team) join instead.
- **MNF 2026-09-28 PHI@CHI unresolved.** Not a resolver bug: the NFL pipeline
  last ran 09-28 20:28 UTC and the every-6h injury slot is Thu-Sun only, so
  nothing fires between Mon ~10am ET and Tue ~11am ET. Monday night results are
  always ~19h late, which means Tuesday-morning recaps are missing a game.
- **NCAAF: 6 scored games with an ungraded spread, 5 with an ungraded total.**

## How to apply

Before Thursday, in value order:
1. Suppress or shadow football totals. 33.3% on n=48 is not variance.
2. Stop sizing off conviction until it is recalibrated — at minimum stop
   treating 80+ as the top tier when it is the only losing bucket.
3. Gate NCAAF underdogs.

NOT yet measured: per-lens attribution (v3 / v4 / SP+ / LR / Panel accuracy
individually). That is the next layer and would say WHICH model is driving the
conviction inversion. Related: [[project_nfl_v4_no_team_features_927]] (v4 is a
constant, still counted as a lens), [[project_sp_plus_compression_927]],
[[project_ncaaf_dog_bias_926]], [[project_nfl_fav_ml_price_discipline_927]].
