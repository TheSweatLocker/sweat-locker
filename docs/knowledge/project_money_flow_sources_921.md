---
name: project_money_flow_sources_921
description: Money-flow source map as of 2026-09-21. cleatz was live but never plumbed into public_splits_archive. Added fadethepublic (JSON API). VSiN/Action rejected as paywalled. NHL has only one source.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T21:48:02.677Z
---

Built after OddsCrowd went client-side
([[project_oddscrowd_client_render_921]]) and Andy said "go find one".

## Current source map

| source | table | status 2026-09-21 | sports |
|---|---|---|---|
| oddscrowd | external_picks / oc_* | **DEAD** (client-render) | — |
| fadereport | fadereport_signals | live, thin on NFL (3 rows) | all 6 inc. NHL |
| cleatz | cleatz_signals | live, healthy (47 NFL/119 NCAAF/9 MLB) | MLB/NFL/NCAAF |
| fadethepublic | fadethepublic_signals | NEW | NFL/MLB/NCAAF/NBA/NCAAB |

## The correction worth remembering

I first told Andy that losing OddsCrowd left exactly ONE money-flow source.
Wrong — I checked fadereport and the archive's `oc_`/`fr_` columns and
missed cleatz entirely.

The real defect was worse: **archive_public_splits only merged OC + FR**, so
cleatz had never written a single row into `public_splits_archive` since it
started on 2026-08-15. A live scraper the consumer could not see. Wiring it
in took NFL from 3 archive rows to 50 immediately.

Lesson: when a source looks missing, check the PLUMBING before concluding
the scraper is dead — and enumerate every `*_signals` table rather than
trusting the archive's column list to be complete. Same class as the dead
resolvers nothing ever called. See [[feedback_validate_data_reaches_new_code]].

## fadethepublicanalytics.com (the new source)

Public JSON API, no auth, no paywall. Both halves of the split (bet% AND
money%) for ml/spread/total. One request per sport.

Endpoints — **NFL is the unprefixed default route**, everything else is
namespaced. Do not "tidy" it:

    NFL    /api/games          <- NOT /api/nfl/games (404s)
    MLB    /api/mlb/games
    NCAAF  /api/ncaaf/games
    NBA    /api/nba/games
    NCAAB  /api/ncaab/games

Independence verified, which is the whole point of a corroborating source:
NYG@LAR moneyline read bets 16% / money 23% here vs fadereport's 15% / 26%.
Two feeds agreeing to the decimal would be one feed in two hats.

Integrity: NFL 96/96 and MLB 18/18 percentage pairs summed to 100. Two
NCAAF rows did not (low-liquidity FCS games, one side at 0%) — the scraper
drops any split that doesn't sum, since a fake divergence feeds the FADE
gate directly.

## Rejected on evidence

  * **VSiN** (data.vsin.com/<lg>/betting-splits/) — exactly the right shape
    (Handle% + Bets% for all three markets, server-rendered HTML table) but
    PAYWALLED: one free sample game, the rest absent from the HTML entirely
    (`paywall` x15, `locked` x16). Would need a VSiN Pro subscription — the
    industry-standard feed if Andy ever wants to pay for it.
  * **Action Network** — `is_free: false`, `__NEXT_DATA__` carries
    book-by-book odds but no bet%/money% fields.
  * Covers 404, OddsTrader 404, ScoresAndOdds/TeamRankings no percentages.

## Attribution design (matters for NCAAF)

Andy: "can barely keep up with fuzzy matching school names." So the scraper
resolves via the curated alias tables — `nfl_team_aliases` (32) and
`ncaaf_team_aliases` (**1,872 rows**: canonical_name, full_name, nickname,
alt_names, abbrev) — with EXACT matching. A name absent from the table
resolves to nothing and the row stays unattributed. Refusing beats guessing;
any last-word or substring matcher conflates Iowa with Northern Iowa.

Two bugs found wiring it:
  * NFL games come with `start_time: null` AND the feed serves the whole
    most-recent slate, so a Monday pull carries Sunday's 15 games. Matching
    against today's one-game context gave 3/48. Fixed by scanning a date
    window (-4/+7) requiring a UNIQUE hit. Now 48/48.
  * Rows are dated to the GAME not the pull, else every splits-vs-result
    join is a day off on carried-over slates.

Final: NFL 48/48, MLB 9/9, NCAAF 208/216.

## Open items

  * **Migration 20260921b NOT APPLIED** as of writing. `latest_ftp` returns
    0 rows until it is (degrades gracefully, no error).
  * **NHL has only ONE source** (fadereport) heading into the Oct 8 opener —
    fadethepublic has no hockey endpoint and cleatz doesn't cover it. The
    2+ source FADE gate cannot fire for hockey.
  * **Arizona State @ Kansas home/away REVERSED** between our
    ncaaf_game_context and this feed. Scraper reports it and refuses.
    Someone should determine which side is right — cf.
    `20260919f_football_unordered_pair_unique.sql`.
  * The FADE gate ([[feedback_sharp_money_discipline_802]]) still counts
    OC + FR only; it should be updated to count any 2 live sources.
