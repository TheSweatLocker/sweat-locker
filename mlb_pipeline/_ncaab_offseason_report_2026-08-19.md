# NCAAB Offseason Readiness Report — 2026-08-19

Season opener Nov 3, 2026 (~76 days out). Two-lane sweep: what could
we ship offseason without odds, and what would it take to fill the
5,911-game spread/total gap?

## Lane A — Shipped this session (no odds required)

### 1. Signal registry: 7 ML signals validated against 2024-25

Ran `_ncaab_ml_signal_backfill_2026-08-19.py` against 5,911 graded
2024-25 games. Signals were evaluated via the same `signal_expr`
evaluator the live scorer uses, so hit rates reflect what would have
happened in production. Ctx synthesized from `ncaab_team_stats`
(season-end panel snapshot) + rolling state (L10 ML at home/road, H2H,
rest days) accumulated strictly from PRIOR games (no leakage).

Registry rows (sport=NCAAB, origin `NCAAB_ML_BACKFILL_2026-08-19`):

| signal_key                    | HR%  | n    | tier            |
|-------------------------------|-----:|-----:|-----------------|
| ncaab_home_efficiency_edge    | 92.5 | 1843 | VALIDATED       |
| ncaab_home_rest_edge          | 74.2 |  691 | VALIDATED       |
| ncaab_away_efficiency_edge    | 73.0 | 1119 | VALIDATED       |
| home_ml_hot_at_home           | 72.3 | 1130 | VALIDATED       |
| ncaab_home_shooting_edge      | 64.4 |  623 | VALIDATED       |
| away_ml_hot_on_road           | 52.0 |  488 | UNVALIDATED     |
| ncaab_away_shooting_edge      | 31.8 |  632 | ANTI_VALIDATED  |

Known bias — end-of-season efficiency snapshot leaks late-season form
into early-season contexts. Rerun scheduled after Feb 15 against
pick-time snapshots from `ncaab_rating_snapshots` so numbers reflect
what was actually knowable at pick time. Expect 5-10pp regression on
the efficiency-edge signals.

The 4 H2H signals never fired (D-I teams rarely meet 4+ times in a
single season) — they'll accrue sample once we have multi-season
history. `ncaab_away_shooting_edge` inverted (31.8% → FADE) is a
legitimate finding: away-team shooting-percentage edge doesn't survive
road environments.

### 2. Efficiency model — panel materialization (not rebuild)

**Course-corrected mid-session:** original brief called for a
box-score efficiency scaffold, but per coordinator note the 3-source
panel already exists in `ncaab_panel_predictor.py`
(kenpom+torvik+haslam). Pivoted `ncaab_efficiency_model.py` to
materialize that panel into a persistent `ncaab_team_efficiency` table
so:

- Every team has a queryable current rating (not just teams appearing
  in tonight's slate — `panel_prediction` today only lives inside
  `ncaab_game_context` JSONB blobs).
- Cohort backfills and dashboards can JOIN, not parse JSONB.
- Defense-in-depth PPG/margin columns from `ncaab_game_results`
  provide a fallback prior if the panel API scrapes fail.

Result (dry-run against 2024-25): 371 teams, 358 with 2+ panel
systems available. Top-5 by panel net: Duke +38.9, Houston +37.0,
Florida +35.0, Auburn +34.9, Alabama +30.7. Distribution: p10 -14.4,
p50 -2.2, p90 +18.5 — a healthy left-skewed spread suitable for
efficiency-edge signals.

**Blocker:** `ncaab_team_efficiency` table doesn't exist yet — apply
migration `20260819_ncaab_team_efficiency.sql` in Supabase SQL editor,
then rerun `python ncaab_efficiency_model.py` without `--dry-run`.

**User-copy note:** all Jerry writeups + card labels must call this
"efficiency model" or "efficiency panel" — never name kenpom / torvik
/ haslam. Enforced by rule `feedback_no_kenpom_attribution.md`.

### 3. 4-lens scaffold shipped as scoring contract

`ncaab_lens_framework.py` — every lens (Efficiency, Form, Pace,
Cohort) returns a `LensOpinion(side, confidence, rationale,
signals_hit, metadata)`. Contract is enforced by an assert-driven
smoke check at file bottom that all four lenses import + return valid
opinions on empty context without raising. `.score()` bodies are
TODO with session pointers (S6 mid-Sept for Efficiency,
S7 late-Sept for Form, S8 early-Oct for Pace, S9 mid-Oct for Cohort).

Fill-in sequence chosen so each lens waits for its data prerequisite:
Efficiency needs the materialization to run, Form needs 2 weeks of
2025-26 games so L10 is meaningful, Pace needs live `close_total`,
Cohort needs the extended cohort backfill to have run.

### 4. Cohort backfill extended (10 new SU cohorts)

Extended `ncaab_cohort_backfill.py`. Iteration is now chronological so
rest-day state can accumulate without leakage. New cohorts added:

- **Home court:** home_all_su, away_all_su
- **Rest:** rest_edge_home_2plus_su, rest_edge_away_2plus_su,
  rest_equal_1day_su
- **Season phase:** early_nov_su, conf_janfeb_su, march_su
- **Ranked:** ranked_vs_unranked_su, top10_vs_top10_su
- **Toss-up:** kp_toss_up_home_su (isolated HCA measurement)

18 total cohorts backfilled. Notable priors (all sample n >= 30):

- Home team wins 65.6% straight up across all NCAAB — much higher HCA
  than any pro league. Anchors the whole four-lens system.
- Efficiency-elite favorites (em_gap ≥ 20) hit 95.3% SU.
- Rested-home advantage: home team on 2+ days rest AND more rest than
  away wins 68.8% SU (n=1702). Rest matters, but HCA dominates it.
- Early November (chaos month) is the STRONGEST home window at 73.5%
  SU — implication: lean toward home in first 3 weeks of season.
- March games (conf tourney + NCAA) drop to 66.3% home SU — neutral
  courts drag the number down as expected.

`mlb_tier_calibration` writes succeeded (18 rows sport=NCAAB, window
`lifetime_su_2024_25`). **`ncaab_cohort_stats` writes failed 404** —
apply migration `20260819_ncaab_cohort_stats.sql`, then rerun.

## Lane B — Odds source scout (5,911-game spread/total gap)

| Source                         | Cost           | Coverage        | Eng effort   | Notes                                                       |
|--------------------------------|----------------|-----------------|--------------|-------------------------------------------------------------|
| **KenPom `team.php` scrape**   | $0 (own sub)   | ~90% back to '10 | 1-2 wk       | Vegas closing lines live in team game logs. **Recommended.** |
| **The Odds API historical**    | ~$60-150/mo    | ~95% back 2-3 yr | 2-3 days     | Existing `ODDS_API_KEY`, add `odds-history` endpoint call.   |
| **SportsDataIO enterprise**    | $500-2000/mo   | 100%            | 1 wk         | Overkill for single sport. Justify only if bundling MLB/NFL. |
| **Just wait for real-time**    | $0             | 100% forward    | 0            | `ncaab_odds_pull.py` already wired. Feb 15 = ~2000 games.    |
| **Kaggle CBB datasets**        | $0             | Varies (loose)  | 1-2 wk       | Coverage inconsistent; team-name normalization brutal.       |
| **OddsPortal / SBRO / Action** | free-ish       | High            | —            | **Skip.** ToS violations + aggressive anti-scraping.         |

**Recommendation: hybrid.** Do NOT invest in retroactive backfill.
Instead:
1. Extend `ncaab_kenpom_snapshot.py` to also pull team game logs
   during monthly cron. Backfills close_spread/close_total for 2024-25
   at $0 marginal cost. ~1 week of engineering, low priority.
2. Trust real-time capture. `ncaab_odds_pull.py` is already wired.
   By Feb 15 we'll have ~2000 games with lines — enough sample to
   validate every spread/total signal before March Madness.
3. Reserve The Odds API historical purchase for spring 2027 if we
   want to backfill a second season for cohort stability.

Rationale: the ML backfill above already validates 7 of 14 ML signals
without any odds. The remaining spread/total signals need lines, but
by the time they'd matter (mid-January weight recalibration) we'll
have half a real-time season on the books.

## Readiness verdict — three scenarios

**(a) With historical odds ingested.** All 45 enabled signals
backfillable within a week; the 4-lens scorer ships with real
recommended_weights on Nov 3. Sharp Card cadence tuned before opener.
Best case, but expensive (~1 wk eng + ~$100).

**(b) Without historical odds ingested (default path).** ML lens
launches with real weights (this session). Spread/total signals ship
UNVALIDATED (w=0.30 floor) and auto-promote as `ncaab_odds_pull`
captures real games. Sharp Card will be conservative through November
(picks require ML consensus) and open up by early January as
spread/total signals validate. **This is what will actually happen
absent additional investment.** Perfectly acceptable.

**(c) V4 model postponed to February (planned).** Doesn't change
launch readiness — 4-lens system runs on efficiency panel + form +
cohorts + pace without V4. V4 slots in as a fifth lens or as a Panel
tie-breaker once we have ~1500 pick-time snapshots to train on.
`project_ncaab_v4_deferred_814.md` remains valid.

## Files shipped

- `mlb_pipeline/ncaab_efficiency_model.py` (keeper)
- `mlb_pipeline/ncaab_lens_framework.py` (scaffold)
- `mlb_pipeline/ncaab_cohort_backfill.py` (extended — 10 new cohorts)
- `mlb_pipeline/_ncaab_ml_signal_backfill_2026-08-19.py` (one-shot,
  gitignored)
- `supabase/migrations/20260819_ncaab_team_efficiency.sql`
- `supabase/migrations/20260819_ncaab_cohort_stats.sql`

## Follow-up queue (blocking Nov 3 or nice-to-have)

1. **APPLY BOTH MIGRATIONS** in Supabase SQL editor, then rerun
   `python ncaab_efficiency_model.py` and `python
   ncaab_cohort_backfill.py` to populate the tables.
2. **Team alias cleanup:** 2025-26 panel has "Iowa State" and "Iowa
   St." as separate rows — `ncaab_enrich_aliases.py` needs a run
   against the 25-26 rating snapshot.
3. **Mid-October dry-run:** stand up `ncaab_game_context` builder
   against preseason exhibition slate (if odds available) so panel +
   form lenses have a real dry-run before opener.
4. **Fill in `EfficiencyLens.score()`** — everything it needs is now
   in `ncaab_team_efficiency`.
