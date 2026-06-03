# Sweat Score — Exact Layout

Frozen as of 2026-06-03. This is the authoritative reference for how the headline sweat score is computed and surfaced.

---

## TL;DR

Every game produces **three sub-scores** (SIDE / TOTAL / PROP), each on a 0–100 scale.
The **headline sweat score** is `max(side, total, prop)`.
Each sub-score maps to a tier: `PRIME ≥ 80 · STRONG ≥ 65 · LIGHT_LEAN ≥ 50 · PASS < 50`.
The dimension whose sub-score equals the headline is the **winning dimension**, and its `play` (if populated) becomes the **model_play** the app shows as the headline call.

The function lives in [play_of_day.py:383 `score_mlb_game`](../play_of_day.py#L383). The DB writer is [play_of_day.py:123 `write_sweat_score`](../play_of_day.py#L123).

---

## The three dimensions

### SIDE — moneyline / spread / runline
The game has an edge on which **team wins** (or covers the runline). Drivers are signals about which side is mispriced.

### TOTAL — Over / Under (and NRFI/YRFI deweighted)
The game has an edge on the **run total**. Drivers are signals about scoring environment, pitcher matchup, prop direction confluence.

### PROP — at least one standout player prop on this game
The game has props worth posting individually, even if the side/total edge is muted. Drivers are tier-count of PRIME/STRONG props with book-line verification.

Splitting into 3 dims was the 2026-05-29 fix for the pre-split bug where total-only edges (e.g. ATL/CIN +0.93 total delta with 4 aligned props scoring 38 PASS) got washed out by the side weakness.

---

## Sub-score arithmetic

```
sub_score = min(100, 30 + sum(driver.points for driver in dim))
```

Each dimension starts at **30** (the "this is a game" baseline). Drivers are additive. Above 80 a PRIME-play floor can kick in (see "Floors and caps" below).

---

## Tier thresholds (per dimension, same on every dim)

| Tier | Score | Meaning |
|---|---|---|
| PRIME | ≥ 80 | Stacked conviction. Eligible for POTD if the dim has an actionable `play`. |
| STRONG | ≥ 65 | Real edge, surface in app. |
| LIGHT_LEAN | ≥ 50 | Worth noting, not card-grade. |
| PASS | < 50 | No edge surfaced. |

The PRIME tier additionally requires the dimension's `play` to be populated — score ≥ 80 without an actionable play caps to STRONG ([play_of_day.py:982 `_dim_tier`](../play_of_day.py#L982)).

---

## SIDE drivers (positive points only)

| Signal | Threshold | Points |
|---|---|---|
| **Elite confluence** | `signal_confluence_net` ≥ 6 | **+18** *(new 2026-06-03)* |
| PRIME confluence | ≥ 5 | +14 |
| Strong confluence | ≥ 4 | +10 |
| Confluence edge | ≥ 3 | +6 |
| Confluence lean | ≥ 2 | +3 |
| v3 market disagreement | \|v3_signed\| ≥ 2.0 | +13 |
| v3 trap-zone (rescued by Jerry) | 1.5–2.0, Jerry agrees ≥ 2.0 | +6 |
| v3 spread edge | ≥ 1.0 | +8 |
| v3 spread lean | ≥ 0.5 | +3 |
| Jerry market disagreement | \|jerry_signed\| ≥ 2.0 | +13 |
| Jerry spread edge | ≥ 1.5 | +8 |
| Jerry spread edge | ≥ 1.0 | +5 |
| Jerry spread lean | ≥ 0.5 | +2 |
| Offense drift gap | hot/cold split ≥ 1.8 | +8 |
| Offense drift edge | ≥ 1.2 | +5 |
| Offense drift lean | ≥ 0.8 | +3 |
| K-gap large | ≥ 12 pts | +6 |
| K-gap edge | ≥ 8 pts | +3 |
| Elite mastery vs opp | pitcher ERA vs team ≤ 1.8 | **+8** *(new 2026-06-03)* |
| Pitcher mastery vs opp | ≤ 2.5 | +5 |
| Pitcher edge vs opp | ≤ 3.0 | +3 |
| **Pitcher torched by opp** | ≥ 8.5 ERA | **+8** *(new 2026-06-03)* |
| Pitcher tagged by opp | ≥ 7.0 | +5 |
| Pitcher struggles vs opp | ≥ 6.0 | +3 |
| PRIME primary_play bonus | tier=PRIME, type∈{ml,spread,rl} | +20 |
| STRONG primary_play bonus | type∈{ml,spread,rl} | +12 |
| LIGHT_LEAN primary_play bonus | type∈{ml,spread,rl} | +6 |

---

## TOTAL drivers

### NRFI / YRFI (2026-05-30 demoted; lives in supplementary_play, not headline)

| Signal | Threshold | Points |
|---|---|---|
| NRFI sweet spot | 90–94 | +15 (audit 50%) |
| NRFI edge tier | 88–89 | +11 |
| NRFI volatile | ≥ 95 | +6 |
| NRFI lean band | 80–89 | +7 |
| NRFI lean | 70–79 | +5 |
| YRFI sweet spot | NRFI ≤ 30, max(1st-inn ERA) 6.0–7.9 | +7 |
| YRFI lean | NRFI ≤ 40 | +4 |

### xERA / pitcher quality

| Signal | Threshold | Points |
|---|---|---|
| Major xERA gap | ≥ 2.0 | +14 |
| xERA gap | ≥ 1.5 | +9 |
| xERA gap | ≥ 1.0 | +6 |
| xERA gap (slim) | ≥ 0.5 | +3 |
| Ace duel | both ≤ 3.00 xERA | +10 |
| Quality matchup | both ≤ 3.50 | +5 |

### 1st-inning extremes

| Signal | Threshold | Points |
|---|---|---|
| Fragile starter sweet spot | max(1st-inn ERA) 6.0–7.9 | +8 |
| 1st-inn fragile (8+, noisy) | ≥ 8.0 | +2 |
| One fragile starter | ≥ 6.0 one side | +5 |
| Mutual NRFI lock | both ≤ 1.5 | +6 |
| One NRFI lock | one ≤ 1.5 | +3 |

### Total-model disagreement (v3 + Jerry)

v3 OVER signals get an **OVER skepticism multiplier (×0.6)** when v3 OVER doesn't have v4 cross-model agreement or v4 OVER auto-suppression is on (per `project_v4_over_drift`).

| v3 disagreement | Threshold | Points |
|---|---|---|
| Major total disagreement | ≥ 2.0 | +18 (×mult) |
| Strong total disagreement | ≥ 1.5 | +14 (×mult) |
| Total edge | ≥ 1.0 | +10 (×mult) |
| Total lean | ≥ 0.5 | +6 (×mult) |
| Total slim edge | ≥ 0.3 | +3 (×mult) |

| Jerry disagreement *(reweighted 2026-06-03 — Jerry MAE 2.29 beat v3 MAE 2.71)* | Threshold | Points |
|---|---|---|
| Jerry major total disagreement | ≥ 2.5 | **+17** (was +12) (×mult) |
| Jerry strong total disagreement | ≥ 1.5 | **+13** (was +9) (×mult) |
| Jerry total edge | ≥ 1.0 | **+9** (was +6) (×mult) |
| Jerry total lean | ≥ 0.5 | **+5** (was +3) (×mult) |

### Direction-conflict gate
If `prop_dir` ≠ total_delta direction AND `(prop_prime + prop_strong) ≥ 4` distinct players, the entire total_delta contribution is **suppressed** (set to 0 and surfaced as evidence). Prevents runaway runs-model from publishing a direction the props confluence rejects.

### Park / weather

| Signal | Threshold | Points |
|---|---|---|
| Extreme hitter park | ≥ 115 | +9 |
| Hitter-friendly | ≥ 110 | +6 |
| Slight Over lean | ≥ 105 | +3 |
| Extreme pitcher park | ≤ 88 | +9 |
| Pitcher-friendly | ≤ 92 | +6 |
| Slight Under lean | ≤ 95 | +3 |
| Cold weather | ≤ 45°F | +3 |
| High wind | ≥ 18 mph | +3 |

### Aligned-prop direction (cross-dim signal)

| Signal | Threshold | Points |
|---|---|---|
| Prop confluence | (PRIME+STRONG) ≥ 4 | +18 |
| Prop confluence | == 3 | +14 |
| Prop alignment | == 2 with ≥ 1 PRIME | +6 |

### Primary-play bonus (when primary_play is total/over/under)
PRIME +20 · STRONG +12 · LIGHT_LEAN +6 (same as SIDE).

---

## PROP drivers *(rebalanced 2026-06-03 to lift legit-conviction games out of PASS)*

Book-verified props weigh strongest. The historical bug: a 1-PRIME-no-book game (or 1-STRONG-no-book) scored 30 PASS because the no-book single-prop paths were too small.

| Signal | Threshold | Points |
|---|---|---|
| PRIME book stack | ≥ 3 book-verified PRIME | +30 |
| Multiple PRIME ✓book | == 2 book-verified | +22 |
| PRIME ✓book available | == 1 book-verified | +20 |
| **PRIME mega-stack (no-book)** | ≥ 5 PRIME no-book | **+28** *(new)* |
| PRIME stack (no-book) | ≥ 4 PRIME no-book | **+24** *(was +20)* |
| Multiple PRIME props (no-book) | ≥ 2 no-book | **+16** *(was +14)* |
| PRIME prop available (no-book) | == 1 no-book | **+14** *(was +8)* |

STRONG add-on (fires when `not prime_props` **or** `len(prime_props) == 1`):

| Signal | Threshold | Points |
|---|---|---|
| STRONG ✓book cluster | ≥ 3 book-verified | +14 |
| STRONG ✓book pair | == 2 book-verified | +9 |
| STRONG ✓book available | == 1 book-verified | +5 |
| STRONG prop cluster | ≥ 3 no-book | +5 |
| **STRONG prop pair (no-book)** | == 2 no-book | **+4** *(new)* |
| **STRONG prop available (no-book)** | == 1 no-book | **+3** *(new)* |

---

## Floors and caps

### PRIME primary_play floor — SIDE / TOTAL only
If `primary_play.tier == 'PRIME'` and the type is `ml/spread/rl`, **SIDE floors at 80**. If type is `over/under/total`, **TOTAL floors at 80**. NRFI/YRFI is **excluded** (audit cohort hits 50%, not edge — see 2026-05-30 demotion).

### Headline cap at 79 if no actionable play exists
[play_of_day.py:140 `write_sweat_score`](../play_of_day.py#L140): if `headline ≥ 80` but the winning dimension's `play` is None, displayed score is capped at 79 and `sweat_breakdown.cap_reason = 'no_dimension_play'`. The raw composite is preserved in `breakdown.sweat_score_raw` for audit.

This prevents the "score 93 but tier STRONG" UI mismatch from 5/29 SF@COL.

---

## How each dimension picks its `play`

### SIDE
1. `primary_play` if type ∈ {ml, spread, rl}.
2. Jerry-driven fallback when SIDE ≥ 65 — needs `|jerry_signed| ≥ 1.0` for a direction, falls back to v3 if Jerry missing. Tier mirrors dim score. *(5/31 fix; backed by Jerry day-1 ML 13-2.)*
3. Otherwise None (still scored, but UI shows no actionable label).

### TOTAL
1. `primary_play` if type ∈ {over, under, total}.
2. Prop confluence override — if `(prime + strong) ≥ 4` aligned distinct players, `prop_dir` becomes the total play.
3. Total delta lean — `|total_delta| ≥ 0.5` and not suppressed.
4. NRFI/YRFI never fills this slot (5/30 demotion); they go to `supplementary_play`.

### PROP
1. Aligned `prop_dir` with ≥ 2 PRIME+STRONG distinct players → `PROP_OVER` / `PROP_UNDER`.
2. Else first PRIME prop available → `PROP_PRIME` with top conviction player.

---

## Headline + winning dimension

```python
dim_table = [
  ('side',  side_score,  side_tier,  side_play),
  ('total', total_score, total_tier, total_play),
  ('prop',  prop_score,  prop_tier,  prop_play),
]
dim_table.sort(key=lambda x: (-x[1], dim_order[x[0]]))   # tiebreak SIDE > TOTAL > PROP
winning_dim_name, headline_score, _, winning_play = dim_table[0]
```

The headline score, headline tier (mapped from headline score), winning dimension, and winning play together produce `dimensions['model_play']`.

---

## Where it's surfaced

| Surface | Reads | What you see |
|---|---|---|
| `mlb_game_context.sweat_score` (INT) | DB | Headline number on game card. |
| `mlb_game_context.sweat_tier` (TEXT) | DB | Headline tier badge (PRIME/STRONG/LIGHT_LEAN/PASS). |
| `mlb_game_context.sweat_breakdown` (JSONB) | DB | `dimensions.{side,total,prop}.{score, tier, drivers, play}` + `winning_dimension`, `model_play`, `supplementary_play`. Feeds WHY THIS SCORE block + dim panels. |
| Sweat Card top-level games | App reads `sweat_score`, `sweat_tier`, `sweat_breakdown.dimensions.model_play.label` | Tier ribbon + headline call. |
| WHY THIS SCORE section | Drivers list from active dim | Per-driver `emoji label +points · detail`. |
| Per-dim tabs | Each dim's drivers / play / tier | Numbers panel split. |
| Card builder picks | Filters games where `sweat_tier ∈ {STRONG, PRIME}` | Tonight's 5-pick eligibility pool. |

---

## Common "why is this PASS?" failure modes (and the calibration fix history)

1. **Game has signals but each driver caps under 10 pts** — Pre-2026-06-03: 4-confluence signals only paid +10; tagged-starter 9+ ERA paid +5 (same as 7+). Now 6+ confluence = +18, ≥8.5 tagged = +8. Lifts genuine stacks out of PASS.

2. **Game has only no-book PRIMEs/STRONGs** — Pre-2026-06-03: 1 STRONG no-book = 0 pts, 1 PRIME no-book = +8. Single conviction props on no-book lines left games at PASS. Now 1 STRONG no-book = +3, 1 PRIME no-book = +14.

3. **Pitcher props (Phase 2 attach) demoted to SKIP** — Confirmed not a grading bug. Pitcher props without book lines are correctly suppressed by Phase 2 (`project_phase2_recal_audit_pending`) because the internal-line-only K/ER/Outs/BB props were the recurring trust-killer pattern (5/30 Brandon Young). They are graded; they're just not visible in the user surface because they're SKIP tier. To make a pitcher prop visible: attach a book line OR mark it PRIME via book-verified path.

4. **Schema cache stale (mastery columns missing)** — Validated by [schema_validator.py](../schema_validator.py). Pre-flight check in [check_pipeline_health.py](../check_pipeline_health.py). If columns return null PostgREST, audit run fails fast instead of silently scoring zero.

5. **`book_line` not in pre-fetch SELECT** — 6/2 bug in [play_of_day.py f39531c](https://github.com/anthropics/claude-code). Pre-fetch query must include `book_line` or the PROP dim sees every prop as no-book. Fixed; regression covered by per-cohort PROP tier audit.

---

## Change log (most-recent first)

- **2026-06-03** PROP no-book rebalance; SIDE 6+ confluence rung + 8.5+ tagged-starter rung; Jerry TOTAL bands bumped roughly to v3-equivalent.
- **2026-06-01** PROP book-aware split; PRIME ✓book / no-book distinction; STRONG ✓book additive ladder.
- **2026-05-31** Jerry SIDE drivers added (independent of v3); Jerry-driven SIDE play fallback; offense drift differential as SIDE driver.
- **2026-05-30** NRFI demoted from primary_play / POTD eligibility; OVER skepticism multiplier for v3/v4 OVER drift; direction-conflict gate.
- **2026-05-29** Three-dim split (SIDE/TOTAL/PROP); aligned-prop direction driver; NRFI weights halved.
- **2026-05-25** `sweat_breakdown` JSONB; WHY THIS SCORE UI parity.
- **2026-05-16** Full scorer rewrite to break out of the 42-45 PASS cluster.
