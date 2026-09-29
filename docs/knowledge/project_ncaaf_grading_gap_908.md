---
name: project-ncaaf-grading-gap-908
description: "🚨 9/8: NCAAF grading gap — 59/70 Sat games ungraded because ncaaf_game_results has ~90 rows total vs 454 CFBD games. Resolver PATCHes existing rows only, never INSERTs. Fix before Sat 9/13."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T12:07:53.619Z
---

**Discovered 2026-09-08 morning audit.** User asked for full grading audit after POTD + Sharp Card + Steam Room appeared ungraded. MLB clean, POTD gap fixed via new `grade_potd.py`. NCAAF is the remaining structural issue.

## Symptom
- `jerry_reads` sport=NCAAF game_date=2026-09-06: 70 rows total, 11 graded, **59 ungraded (84%)**
- Also 9/5: 0/92 graded. 9/4: 0/51 graded.
- Even after running `resolve_ncaaf_results.py` manually today, only 1 additional read graded.

## Root cause
- `resolve_ncaaf_results.py`:
  - Pulls 454 CFBD games with scores
  - Matches by name → 90/454 matched (exact) + 6 fuzzy = 96 total
  - PATCHes those 96 game_results rows with fresh scores
  - **Never INSERTs a new row** — the resolver is patch-only
- `ncaaf_game_results` table has ~90 total rows across the season vs. 500+ NCAAF games that have `jerry_reads` referencing them
- `grade_jerry_reads.py --sport NCAAF` reads scores from `ncaaf_game_results` only, so any jerry_read whose game_id has no ncaaf_game_results row STAYS UNGRADED forever
- The upstream NCAAF pipeline (`ncaaf_pipeline.yml`) never seeds `ncaaf_game_results` with placeholder rows for the games it creates jerry_reads for

## Blast radius
- Every Sunday morning: 50-70 NCAAF Sat games sitting ungraded in the app's Receipts / Sharp Card / Ledger records for NCAAF
- Cumulative silent gap since NCAAF pipeline shipped (2026-08)
- Users see "NCAAF: 0 record" or vastly under-counted totals on any NCAAF-facing surface

## Fix options (ranked)

**Option A (recommended, ~1 hr):** Modify `resolve_ncaaf_results.py` to UPSERT instead of PATCH. When resolver matches a CFBD game to a game_id from `ncaaf_game_context` or `jerry_reads` (not just existing `ncaaf_game_results` keys), INSERT the row with fresh scores + outcome computations. Resolver becomes authoritative source of truth for game results.

  - Change matching logic: build the "candidate game_id" set from union of `ncaaf_game_results.game_id` + `ncaaf_game_context.game_id` + `jerry_reads.game_id` where sport=NCAAF
  - Add UPSERT payload with computed fields (home_win, total_result, spread_result, overtime, total_points)
  - Verify PostgREST on_conflict param works for the unique key on game_id

**Option B (~2 hrs):** Add a pre-game step to `ncaaf_pipeline.yml` that INSERTs placeholder `ncaaf_game_results` rows for every game it creates a jerry_read for. Resolver then PATCHes as-is. More work, less risky refactor.

**Option C (~30 min, minimal):** Add a bulk-backfill script that seeds all historical NCAAF game_results rows from `ncaaf_game_context` + CFBD one-time. Fixes the historical gap but doesn't prevent future recurrence — bad long-term.

## Fix priority
- **Before Sat 9/13** (next NCAAF game day) — user expects to see graded records Sunday morning
- Non-blocking for launch (app already submitted) but visible to any user who taps NCAAF Receipts

## UPDATE 2026-09-08 late: deeper root cause found

The resolver improvements (41f656ae + 59a74f2f) shipped THREE passes:
1. CFBD-forward PATCH (existing rows w/ known alias)
2. CFBD-forward UPSERT (new rows via ctx/jerry_reads coverage)
3. Reverse-sweep from jerry_reads (fuzzy match by date+fold pair)

All three combined only closed the gap by a handful of games. The reverse-sweep for Sat 9/6 caught 3 additional, but 55 jerry_reads for 9/6 STILL couldn't find CFBD matches.

**Investigation:** those 55 are FUTURE games (Week 2 or 3) that ncaaf_odds_pull mistagged with the 9/6 date. Verified with Bowling Green @ Nebraska:
- jerry_reads: `ncaaf_20260906_Bowling Green_Nebraska` (game_date = 9/6)
- CFBD schedule: 2026-09-12 (Week 2), scores not yet available (game not played)

The odds API returned commence_time for Week 2/3 games, and ncaaf_odds_pull assigned them all game_date = 9/6 (probably because it defaulted to current week without checking event kickoff date).

**The permanent fix isn't in the resolver — it's in ncaaf_odds_pull.py.** Needs to use the event's actual kickoff_utc → ET-date truncation, not "current week Sat" as a fallback.

Once ncaaf_odds_pull correctly dates each game, the resolver's three passes will pick everything up cleanly. Until then, 70-80% of jerry_reads for any given Saturday are "future games mis-dated to yesterday" and can't be graded because they haven't happened.

**Priority ladder:**
1. FIX ncaaf_odds_pull date logic (this is the root)
2. Backfill script to correct game_date on existing NCAAF jerry_reads (based on CFBD scheduled kickoff)
3. Then resolver's three passes work as designed

## Related
- [[project_nfl_prop_signal_gap_908]] — sibling gap on NFL prop side
- [[docs/MORNING_BRIEF.md]] — spec for what should be checked every morning
- resolve_ncaaf_results.py line 254 — current patch-only logic
- grade_jerry_reads.py line 30 (`'NCAAF': 'ncaaf_game_results'`) — grader's source-of-truth table
