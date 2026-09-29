---
name: ufc-pipeline-broken-726
description: "UFC pipeline broken since 5/21 — scraper populating event headers with fights_count=0, scorer producing tier=None on all outputs. Full rebuild needed before UFC can feed Daily Degen. Dedicated sprint queued for next session."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-27T01:35:04.959Z
---

**Set 2026-07-26 EOD after UFC audit blocked Daily Degen cross-sport integration.**

## Live state (verified 7/26)

| Component | Status | Detail |
|---|---|---|
| `ufc_upcoming_event` | 🔴 stale + broken | 7 rows tracked, ALL have `fights_count=0`. Last scrape 2026-05-21. |
| `ufc_picks` | 🔴 nulls | 44 rows total. Latest 8 (June 6) all `tier_winner=None`, `recommended_side=None`, all p_* fields null. |
| `ufc_fighter_stats` | ✅ | 4466 rows — fighter DB intact |
| `external_picks` sport=UFC | 🔴 | **0 rows total** — no `pull_externals_ufc.py` exists |
| `ufc_score_card.py` runs | Runs but no-op (0 fights to iterate) |
| `ufc_resolve_fights.py` | Runs Sun/Mon, nothing to resolve |
| `generate_ufc_game_reads.py` | Self no-ops |

## Root causes

1. **Scraper `ufc_card_scraper.py`** — reads event header but `fights` array
   comes back empty. Likely UFCStats.com HTML structure change since
   ~5/21. Scraper writes row w/ empty fights, cron thinks it succeeded.
2. **Scorer** — depends on `ufc_upcoming_event.fights` being populated;
   with empty fights it produces nulls. Not scorer's fault — upstream data.
3. **No external aggregator** — `pull_externals_ufc.py` never built.
   MMA has robust public analyst market (BestFightOdds, Doc Sports MMA,
   Covers MMA) we're missing entirely.

## Dedicated UFC sprint scope (~4-6 hrs)

1. **Scraper fix** (~2h) — inspect UFCStats.com structure, rewrite fight-
   list selectors, verify fights_count > 0 on a real card.
2. **Scorer validation** (~30 min) — once scraper produces real fights,
   confirm `ufc_predict.predict_fight` returns numeric probs and
   `tier_winner` propagates.
3. **Build `pull_externals_ufc.py`** (~2h) — port the MLB externals
   template. Target: BestFightOdds, Doc Sports, Covers MMA.
4. **Add UFC leg extractor to `generate_daily_degen.py`** (~30 min) —
   pull `ufc_picks` PRIME/STRONG rows on card days, format matching the
   ML/PROP leg shape so `extract_leg_candidates` accepts them.
5. **Validate on one live card** before merging Daily Degen extension.

## Why NOT partial-fix now

Fresh model predictions need at least one live card to validate before
we trust them for Daily Degen. Half-shipping = pushing stale/broken
picks to users. Full rebuild + one-card validation is the honest path.

No urgency — Daily Degen currently blends MLB only, works fine.
User can decide UFC addition timing based on model performance
after the sprint.

## Related

- [[project_ufc_328_calibration]] — historical UFC 328 baseline
- [[feedback_everything_means_all_lenses]] — UFC would add to lens count
