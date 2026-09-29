---
name: ncaab-phase1-complete-724
description: NCAAB Phase 1 core loop shipped 7/24 — 6 scripts + RLS migration + workflow. Ready for Nov 2026 season. 361/365 aliases enriched with ESPN + Odds API IDs; 965 games backfilled from 2024-25.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-26T02:36:40.467Z
---

**Set 2026-07-24 evening after full NCAAB Phase 1 completion.**

## Shipped (SHA cc48e55)

**Foundation**
- `supabase/migrations/20260723d_ncaab_rls.sql` — RLS + permissive
  anon policies on 4 NCAAB tables. Foundation migrations shipped
  without RLS; pipeline writes would 401 the moment RLS gets enforced.

**Alias enrichment (fixes historical name-drift issue)**
- `ncaab_enrich_aliases.py` — one-time bootstrap. Populates espn_id +
  odds_api_name + rich alt_names on 365 alias rows. Uses:
  - MANUAL_ESPN_TO_CANONICAL dict for 30 known edge cases (UConn↔
    Connecticut, App State↔Appalachian St., NC State↔N.C. State, etc.)
  - Deterministic normalization variants (State↔St., Saint↔St.,
    ampersand, punctuation) for algorithmic matches
  - NO fuzzy-distance guessing — mismatches go to structured audit
- Result: **361/362 ESPN teams matched, 361/365 aliases enriched**
- 4 orphans (Queens, Lindenwood, Southern Indiana, Saint Francis) are
  recent D1 transitions ESPN's teams roster doesn't yet include

**Live pipeline**
- `ncaab_odds_pull.py` — Odds API basketball_ncaab → ncaab_game_results
- `resolve_ncaab_results.py` — ESPN scoreboard nightly resolver
- `ncaab_backfill_results.py` — ESPN historical scoreboard backfill
- `generate_ncaab_game_reads.py` — Jerry (mirrors NCAAF pattern)

**Workflow**
- `.github/workflows/ncaab_pipeline.yml` — 4-cron nightly:
  - 10am ET daily → build slate (odds + context + Jerry + externals)
  - 4pm  ET daily → pre-tip refresh
  - 7am  ET daily → overnight resolver (last night's finals)
  - 11am ET Monday → weekly KenPom refresh

## Live DB state (post-tonight)

| Table | Rows |
|---|---|
| ncaab_team_aliases | 365 (361 enriched w/ espn_id + odds_api_name) |
| ncaab_team_stats | 365 |
| ncaab_game_results | 965 (2024-25 season backfill) |
| ncaab_game_context | 0 (season starts Nov 2026) |

## Backfill limitations (known, deferred to Phase 1b)

1. **ESPN scoreboard = curated slate**, not full D1. 965 games backfilled
   vs ~5,500 total D1 games/season. Bias toward major-conference. Fix:
   pass `?groups=50` param or iterate ESPN teams endpoint per team.
2. **ESPN rarely attaches odds** for CBB. Backfill rows have scores but
   often NULL close_spread/close_total. Historical odds requires paid
   Odds API tier or KenPom scrape.
3. **NCAAB prompt_templates row missing** — Jerry falls back to NBA
   rules template. Cross-sport Jerry audit will unify.

## Name-mapping safeguards (per user callout)

User flagged: "before we had backend we stored names in index, had a
few name mix ups that never got resolved so want to make sure that
issue is resolved."

Response: STRICT alias-table lookup only. Never fuzzy. Every
mismatch appends structured JSON entry to `ncaab_alias_gaps.log`
with ESPN ID + display name — impossible to silently mis-attribute.
New drift in Nov season → add to MANUAL_ESPN_TO_CANONICAL, re-run
enrich, done.

## Related

- [[project_ncaaf_phase1_complete_723]] — mirror pattern (CFB)
- [[project_ncaab_scope]] — v1.0 spreads/totals/ML only, no props
- [[project_external_aggregation_launch]] — external picks tab
- [[feedback_postgrest_batch_normalize_keys]] — batch upsert pattern

## Followups queued

- ~~Apply migration `20260723d_ncaab_rls.sql`~~ (DONE 7/24)
- ~~Monday cron KENPOM_KEY probe~~ (VERIFIED 7/24 via local pull — 365 teams 2025-26, 364 teams 2024-25 both landed)
- ~~Broader backfill via ESPN `?groups=50`~~ (Phase 1b DONE 7/25 SHA f59dc09 — 965→5,911 games)
- ~~Four Factors surfacing in game detail~~ (Phase 1c DONE 7/25 SHA e9e5547 — Team Stats tab uses ncaab_team_stats with 10-row eFG/TO/OR/FTR + adj OE/DE + rank badges out of 365; Luck+SOS already in Situational tab)
- Historical odds source (Phase 1d — paid Odds API or KenPom scrape)
- NCAAB prompt_templates row (cross-sport Jerry audit)
- Daily KenPom snapshot capture (fixes end-of-season bias in cohort SU rates)
- Phase 1d (Oct): Barttorvik puller + NCAAB Monte Carlo + rest-days signal

## Cohort baseline — REFRESHED 7/25 with full D1 sample (n=5,911)

Phase 1b shipped ?groups=50 fix (SHA f59dc09) — full D1 coverage,
went from 965 → 5,911 games. Cohort backfill re-ran on 99.9% join.

| Cohort | Old n=965 | New n=5,911 | Read |
|---|---|---|---|
| Elite fav SU 20+ | 98.3% | 95.3% (n=751) | -3pt at 3x sample = honest |
| Road fav SU 20+ | 92.7% | 84.5% (n=181) | -8pt drop = old was noise |
| Strong fav SU 10-19 | 86.2% | 84.5% (n=1,679) | Stable |
| Road fav SU 10+ | 78.0% | 76.4% (n=890) | Stable |
| Slight fav SU 5-9 | 68.6% | 70.3% (n=1,571) | +2pt |
| Home overall SU | 68.7% | 65.6% (n=5,911) | -3pt (cleaner, no major-conf skew) |
| **HCA (evenly matched)** | 62.5% (n=120) | **63.4% (n=1,197)** | **VALIDATED at 10x sample** |

**HCA baseline is 63.4% at n=1,197.** Rock-solid pre-game gate for
Nov 2026 season.

Info-only: 1,100 home blowouts (20+) vs 217 away = 5.1x asymmetry
(consistent with prior finding); slow-pace avg 131.8 pts (n=367).

All KP-fav SU rates still slightly INFLATED by end-of-season snapshot
bias (final KP knows who won). Real live during Nov 2026 will be
another 5-10pt lower on the fav rates. HCA baseline is UNAFFECTED
by snapshot bias (evenly-matched controls for team quality).
