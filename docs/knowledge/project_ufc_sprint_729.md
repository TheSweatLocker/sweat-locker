---
name: ufc-sprint-729
description: "7/29 UFC sprint: shipped resolver ESPN swap + odds pull + EV compute + weekly card. Belgrade (Aug 1) fully wired end-to-end. Method/distance/round market scraping deferred (Odds API tier doesn't expose). NEXT: cron wiring + app surface + DK/FD scrape for prop markets."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-29T22:42:12.540Z
---

**Shipped 2026-07-29 as one-day UFC value-side sprint.**

## What's now working end-to-end

1. **Resolver** — `ufc_resolve_fights.py` (rewrite). ESPN-backed. Live test: 12/12 on 7/25 Ankalaev card.
2. **Odds pull** — `ufc_odds_pull.py`. Odds API v4 h2h + median/best/book_count. Live test: 7/8 Belgrade matched.
3. **EV compute** — `ufc_compute_ev.py`. Tiered picks (PRIME ≥+8 & conv≥30, STRONG +4-7, LEAN +2-3, SKIP).
4. **Weekly card** — `ufc_weekly_card.py`. Markdown or JSON payload w/ lock + method leans.

Migration: `supabase/migrations/20260729_ufc_market_odds.sql` — applied.

Card ready for Aug 1 Belgrade:
- 🔒 **LOCK: Duško Todorović ML +140** · EV +59.5 · PRIME · KO/TKO 38%
- 🔒 PRIME: Daniel Rodriguez ML +300 · EV +16.7 · KO/TKO 49%
- 📊 LEAN: Mateusz Rębecki -667 · EV +3.6 · DEC 56%
- 📊 LEAN: Navajo Stirling -323 · EV +2.0 · DEC 52%

## Weekly runbook (manual for now, cron-wire after Belgrade)

```bash
# Wednesday morning (odds settle):
python mlb_pipeline/ufc_card_scraper_v3.py           # populate ufc_upcoming_event
python mlb_pipeline/ufc_score_card.py                # write ufc_picks (model probs)
python mlb_pipeline/ufc_odds_pull.py                  # pull h2h into ufc_picks
python mlb_pipeline/ufc_compute_ev.py                 # tier by EV

# Friday morning (final):
python mlb_pipeline/ufc_odds_pull.py                  # refresh (line moves)
python mlb_pipeline/ufc_compute_ev.py                 # re-tier
python mlb_pipeline/ufc_weekly_card.py --format markdown  # publish

# Sunday morning (post-card):
python mlb_pipeline/ufc_resolve_fights.py --date <sat-date>  # grade
```

## Deferred / next

1. **Method/round/distance prop markets** — Odds API `mma_mixed_martial_arts` at our tier only exposes `h2h`. `fight_result_method`/`total_rounds`/`to_go_the_distance`/`round_betting` all return 422. Two paths:
   - Scrape DK/FD directly for MMA prop lines (harder, brittle)
   - Upgrade Odds API tier (money, verify markets first)
2. **Cron wiring** — GitHub Actions workflow for Wed/Fri/Sun triggers
3. **UFC surface in app** — sport-specific extension of [[project_game_detail_redesign_729]]. Method/round breakdown card + EV tier badge.
4. **UFC prop pipeline table** — when method markets come online, create `ufc_pipeline_props` with per-market picks (mirrors mlb_pipeline_props). Currently EV lives inline on ufc_picks (one-row-per-fight is enough for h2h-only MVP).
5. **8th Belgrade fight** — "Josias Musasa vs Mark Vologdin" didn't match Odds API. Fighter-name mismatch or fight got cancelled — verify Friday.

## Related
- [[project_ufc_pipeline_broken_726]] — closed 7/26 (scraper); today closed the resolver + built value side
- [[project_ufc_328_calibration]] — historical calibration reference
- [[project_game_detail_redesign_729]] — where UFC card lands in app
