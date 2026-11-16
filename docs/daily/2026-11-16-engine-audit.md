# 2026-11-16 — NBA + NCAAB engine audit: signals, calibration, data flow

Asked: what signals the engine reads, how games are evaluated, tuning and
calibration, plus a full end-to-end check of external handicapper sources,
money flow and team stats.

Everything below is measured by direct query. Two things were **corrected on
the spot** (items 5 and 6); the rest is logged.

---

## 1. 🚨 THE HEADLINE: the engine picks WINNERS, not PRICES

NCAAB, 507 context rows joined to graded finals:

| confluence net | n | ML hit | **ATS hit** |
|---|---|---|---|
| ±1 | 85 | 67.1% | **50.0%** (n=82) |
| ±2 | 164 | 76.2% | **49.0%** (n=151) |
| ±3 | 112 | 75.9% | **47.5%** (n=101) |
| ±4 or more | 106 | **89.6%** | **49.0%** (n=100) |
| all non-zero | 467 | 77.5% | **48.8%** (n=434) |

**ML rises monotonically from 67% to 89.6%. ATS never leaves the coin flip.**
NBA is the same shape (ML 56.9% → 74.5% across the same bands, n=221).

That is the whole story of last week's numbers in one table: NCAAB reads hit
**60.5% on ML and still lost 19.6%** because an 89.6% winner is a price the
market already made. Breakeven at -110 is **52.38%**, and no confluence band
in either sport clears it against the spread.

### The tier ladder is INVERTED for ATS

| NCAAB sweat_tier | ATS | n |
|---|---|---|
| COVERAGE | **55.6%** | 90 |
| LEAN | **54.3%** | 70 |
| PRIME | 50.0% | 100 |
| STRONG | **43.8%** | 121 |
| LIGHT_LEAN | **36.0%** | 50 |

The two tiers the engine is most confident in are the two worst against the
number, and the bottom tier is the best. Same inversion Andy has already seen
on NFL props. All cells are above the n>=30 gate; LIGHT_LEAN is 18-32.

## 2. WHY: the lenses are not independent

A confluence of N signals is only N signals if they can disagree.

**NCAAB pairwise agreement** (507 games):

| pair | n | agree |
|---|---|---|
| `adj_em` vs `defense` | 424 | **94.8%** |
| `efg_exploit` vs `or_exploit` | 111 | **91.0%** |
| `adj_em` vs `efg_exploit` | 202 | 83.7% |
| `defense` vs `efg_exploit` | 181 | 82.9% |
| `defense` vs `or_exploit` | 167 | 80.2% |
| `adj_em` vs `or_exploit` | 197 | 77.2% |
| `adj_em` vs `ftr_exploit` | 182 | 73.1% |
| `adj_em` vs `to_exploit` | 192 | 72.4% |
| — | | |
| `adj_em` vs `sos` | 371 | **33.4%** |
| `defense` vs `sos` | 312 | **29.8%** |
| `adj_em` vs `tempo_control` | 116 | 45.7% |
| `adj_em` vs `luck` | 68 | 60.3% |

Six of the nine NCAAB lenses — `adj_em`, `defense`, `efg_exploit`,
`or_exploit`, `to_exploit`, `ftr_exploit` — are one team-efficiency lens
wearing six hats. Only **`sos`, `tempo_control` and `luck`** carry
independent information. NBA is worse: `elo` / `net_rating` / `def_rating` /
`off_rating` agree 84-91% pairwise, and only `rest` and `back_to_back` are
independent (37-50%).

So `signal_confluence_net` is, in effect, **a team-strength score with a
count on it** — which is exactly why it discriminates ML and not ATS.
[[feedback_lens_independence_needs_different_inputs]]

## 3. Per-signal standalone ATS — where the tuning actually is

Breakeven -110 = **52.38%**. Signals with ATS n<30 omitted.

### NCAAB
| signal | ATS n | ATS | ML | read |
|---|---|---|---|---|
| `sos` | 349 | **52.4%** | 37.5% | breakeven; the only lens that points at the WEAKER team |
| `tempo_control` | 107 | **52.3%** | 47.9% | breakeven |
| `or_exploit` | 187 | 49.7% | 71.9% | no edge |
| `defense` | 394 | 49.2% | **81.7%** | favourite detector |
| `adj_em` | 470 | 48.7% | **78.5%** | favourite detector |
| `efg_exploit` | 191 | 48.7% | 73.8% | no edge |
| `to_exploit` | 179 | 48.6% | 69.8% | no edge |
| `ftr_exploit` | 175 | 47.4% | 65.8% | negative |
| `luck` | 65 | 46.2% | 57.1% | negative |

### NBA
| signal | ATS n | ATS | ML |
|---|---|---|---|
| `net_rating` | 141 | **53.2%** | 65.9% |
| `elo` | 149 | 51.7% | 65.1% |
| `def_rating` | 132 | 51.5% | 64.4% |
| `off_rating` | 121 | 50.4% | 61.4% |
| `back_to_back` | 43 | **41.9%** | 54.2% |

**The two signals with any ATS edge in NCAAB are the two independent ones, and
they are the two the engine barely consults** — `tempo_control` appears on
116 of 507 games, `sos` on 371, while `adj_em` is on 506 of 507.

**`back_to_back` in NBA is 41.9% ATS (n=43)** — below the documented 45%
threshold where [[feedback_fade_not_suppress_803]] says fade the other side.
NOT flipped here: n=43 is above the n>=30 gate but thin for a sign change, and
it belongs in a shadow first ([[feedback_suppression_gate_needs_shadow]]).

## 4. The registry explains how this was allowed to happen

`signal_registry`: NBA **88** signals, NCAAB **72**. Measurement coverage:

| sport | total | sample_n | hit_rate | edge_pp | n>=30 AND edge_pp |
|---|---|---|---|---|---|
| NBA | 88 | 88 | **50** | 38 | **21** |
| NCAAB | 72 | 72 | **22** | 22 | **17** |

NBA is 73 of 88 `UNVALIDATED` with 3 `VALIDATED`; NCAAB 47 of 72
`UNVALIDATED` with **2** `VALIDATED`. **59 of NCAAB's 72 were last computed
2026-11-04** — 12 days stale.

And the two NCAAB `VALIDATED` signals are validated on the wrong axis:

```
ncaab_projected_spread   n= 78  hit=79.49  base=56.38  edge=+23.10  scope=ml
OFF_RATING_GAP           n=219  hit=78.08  base=58.57  edge=+19.52  scope=ml
```

Both are **ml-scope, scored on hit rate against a baseline that is not
price-aware**. My independent pass says those same inputs are 48.7-49.2% ATS.
Both facts are true: the signals really do pick winners, and a 78% winner at
-300 loses money. A +19.5pp "edge" that is pure favourite bias is how a
signal gets promoted to VALIDATED and then loses 19.6% on the moneyline.
[[feedback_flat_110_inflates_every_ml_figure]],
[[feedback_market_agreement_is_not_accuracy]]

Worth noting the externals ARE measured on the spread and do better than the
model lenses: `external:covers` n=74 hit 54.05% edge +4.14 (rl),
`external:pickdawgz` n=64 hit 51.56% (rl).

## 5. ✅ CORRECTED: an HTTP 200 was being logged as a successful pull

**`oddscrowd` — the largest external source in the book, 38,781 picks
all-time — stopped returning anything around 11-10 and nothing noticed for
six days.** 55 of its 70 runs since 11-08 parsed zero picks. Every single one
logged `status=success, http=200`.

Root cause, probed live today: **OddsCrowd removed the Bets%/Money% public
split block from its game pages.** The parser anchors on a doubled header plus
`Bets\n N%\n N%\n Money\n N%\n N%`; the live page now has a book-by-book odds
grid, the header appears once, and there is **not a single `%` character on
the page**. Nor is the data in the payload: `handle`, `consensus`, `bets_pct`
all absent from the embedded JSON. The site dropped it; our scraper is fine.

The defect on our side is that **status came only from the HTTP code** — a
transport fact standing in for an extraction fact.

Fixed at **all five writers** ([[feedback_fix_every_writer_to_the_table]]):
`externals_pro_core.py` (NBA, NHL) and `pull_externals_{ncaab,nfl,ncaaf,mlb}.py`,
each of which had its own `complete_pull_log` hardcoding `'success'`.
`external_pull_log.status` has a CHECK constraint allowing only
success/failed/running, so a new status word would be rejected (23514 — the
documented 09-25 mistake). The distinction goes in `notes` instead, where it
is queryable:

```
external_pull_log?notes=ilike.*ZERO-EXTRACTION*
```

Verified live — ran the NCAAB oddscrowd pull and the row came back:

```
2026-11-16T05:21:54 NCAAB oddscrowd st=success picks=0 games=0 http=200
  notes='ZERO-EXTRACTION: 24 slate game(s), 0 picks parsed, http 200
         - the page likely changed shape or dropped the data'
```

A healthy source in the same window carries `notes=None`.

## 6. ✅ CORRECTED: the detector for this existed and ran nowhere

`audit_external_source_coverage.py` was written **2026-10-10 for exactly this
bug class** — vsin and bettingpros dark inside a hardcoded NCAAF subset — and
was never scheduled in any workflow. Added to
`.github/scripts/nightly_cross_sport.sh` as a `--detector` step beside
`audit_data_quality.py`, read-only so it reports and never blocks.
[[feedback_a_writer_in_no_workflow]], [[feedback_run_the_unscheduled_writer_audit]]

Running it immediately also **retired a false alarm of my own**: I had
`vsin` and `bettingpros` down as broken because they return 0 picks on every
NCAAB/NFL/NCAAF run. They are **documented MLB-only** sources that are merely
*defined* in the other registries. Dark there is correct, not a bug.
[[feedback_a_detector_must_know_the_intentional_case]]

## 7. Money flow: alive, but thinner than it looks

`oddscrowd_snapshot` is still populated — **but every row is tagged
`"source": "so"` (scoresandodds), 94 of 94 NBA and 365 of 365 NCAAB.** The
column name no longer describes its contents; oddscrowd contributes nothing
to it.

`splits_summary` coverage and agreement depth:

| | games with splits | ml mean sources | rl | total |
|---|---|---|---|---|
| NBA | 58/66 | **1.87** (24 of 55 on ONE source) | 2.59 | 2.59 |
| NCAAB | **214/367 (58%)** | **1.45** (96 of 142 on ONE source) | 1.85 | 2.13 |

**Moneyline splits are effectively single-source**, and ML is precisely the
market where both sports are losing. NCAAB money flow is missing on 42% of
games outright.

## 8. Team stats and context fill — end to end

`nba_game_context` 130 columns, `ncaab_game_context` 109. Efficiency,
pace, four-factor, elo, rest and injury columns are ~full in both. Gaps:

- **`ncaab_game_context.home_bart_em` / `away_bart_em`: 0 of 683 all-time, and
  NOTHING IN THE CODEBASE WRITES THEM** (zero references). Dead schema, not a
  broken feed — the populated twin is `adj_em` at 634/683.
  [[feedback_a_zero_fill_column_can_have_a_populated_twin]]
- **`ncaab_rating_snapshots` — `torvik` has 2 snapshots, latest 2026-11-03
  (13 days stale)**, against `haslam` 94 snapshots and `kenpom` 16, both
  current to 11-15. One of three rating systems is not refreshing.
- `matched_patterns` 0% in both; `close_locked_at` and `tournament_round` 0%
  in NCAAB; `sweat_breakdown` 0% in NBA.
- NCAAB `h2h_last5_avg_total` **10.4%**, `h2h_last5_avg_margin` 33.0%.
- NBA 11-08: 7 games with **zero** team_form / tendencies / oddscrowd
  enrichment. A one-day historical hole; 11-09 onward is complete.
- NCAAB `mc_probabilities` spotty before 11-12 (25/29, 25/34, 41/56), full after.

## 9. 🚨 NCAAB context and results live in different ID spaces

```
ncaab_game_context.game_id   '089fe17e00a51999acb43407f067f273'   (32-hex)
ncaab_game_results.game_id   'ncaab_20270307_Arizona_Houston'     (composed)
```

A join on `game_id` returns **0 rows of 683**. I had to bridge on normalised
team names plus a +/-1 day tolerance to calibrate anything, which matched 549
exactly, 12 at off-by-one, and **122 not at all**. Any analysis that joins
these two tables on `game_id` silently measures nothing —
[[feedback_zero_rows_is_a_wiring_error]]. The 12 off-by-one rows are all
phantom future-dated fixtures (ctx 2027-03-06 vs res 2027-03-07).

## 10. Recommendations (not applied — pick composition is Andy's call)

1. **Stop scoring game signals on ML hit rate.** Score on ATS, or on ML
   against the closing implied probability. Today a favourite detector scores
   +19.5pp and gets VALIDATED. This single change reorders the registry.
2. **Collapse the six correlated NCAAB efficiency lenses into one** and let
   `sos`, `tempo_control` and `luck` carry their own weight. A 9-lens
   confluence that is really 4 lenses overstates its own conviction.
3. **Re-weight toward the independent lenses.** They are the only two at
   breakeven, and they are consulted least.
4. **Shadow a `back_to_back` fade in NBA** (41.9% ATS, n=43) before flipping it.
5. **Demote STRONG and LIGHT_LEAN in NCAAB**, or at minimum stop treating them
   as the confident tiers — 43.8% and 36.0% ATS.
6. Rerun the NCAAB registry rescore; 59 of 72 rows are 12 days stale.
7. Decide whether `oddscrowd` stays in rotation. It costs ~70 fetch runs a
   week and the data it existed to scrape is gone from the source. Its slot is
   the ML-splits gap in item 7.
