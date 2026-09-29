---
name: project-ncaaf-ingest-duplicate-902
description: ✅ RESOLVED 9/19. NCAAF duplicate ctx rows — real cause was UTC-vs-ET game_id date drift (NOT mascot names). Repair applied + DB constraints installed and proven enforcing.
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-18T21:49:48.267Z
---

🐛 Duplicate `ncaaf_game_context` rows for the same game.

## ⚠️ The 9/2 diagnosis was WRONG

Original theory: mascot-name drift ("Rhode Island" vs "Rhode Island
Rams") creating two rows. **Disproved 2026-09-18** — a full scan showed
all 13 active duplicate matchups had **different `game_id` values with
identical team names**. Zero shared game_ids. Not a naming bug.

## Real root cause: UTC-vs-ET date drift in game_id

`ncaaf_odds_pull.py` built `game_id` from the **UTC** date while
`game_date` came from the **ET** date. NCAAF Thu/Fri night kickoffs
(8pm+ ET) cross UTC midnight, so game_id landed one day ahead of
game_date. A later pull with a slightly different `commence_time`
wrote a SECOND row under the correct id — same game, two rows,
conflicting spread and tier.

Signature: `game_id` embeds a date that differs from the row's own
`game_date`, always by exactly 1 day.

## Status — ✅ RESOLVED 2026-09-19

- ✅ Write path fixed 2026-09-16 (`ncaaf_odds_pull.py:163-174`, uses
  `et_dt`). Andy's own 9/16 audit caught Texas Tech @ Houston.
- ✅ Repair script written: `mlb_pipeline/repair_ncaaf_gid_date_drift.py`
  (`--dry-run` / `--apply`). Commit d08b6e40.
- ✅ Constraint migration written: `20260918d_ncaaf_gid_date_invariant.sql`
  — CHECK that game_id encodes game_date + UNIQUE on
  (game_date, home_team, away_team).
- ✅ **Repair APPLIED 9/19**: 13 ctx deleted, 1 payload refreshed, 38
  game_ids rewritten, 43 jerry_reads repointed, 13 dup reads removed.
  Duplicate matchups 13 -> 0, gid/date mismatches 52 -> 0.
- ✅ **Migration 20260918d INSTALLED** and verified ENFORCING by live
  test: UTC-drifted game_id rejected (400 check constraint), correct id
  accepted (201), duplicate matchup rejected (409 unique index).
- ✅ Residual phantom resolved: `ncaaf_20260920_Houston_Texas Tech`
  (game_date 9/19, spread -14.0) deleted with its orphan jerry_read.
  ESPN confirmed no such game on 9/19 (71 CFB games) or 9/20 (0). The
  real game was 9/18 at -7.5, final 26-28.
- ✅ `reconcile_resolution.py` (commit 2f9bbf2f) now watches for
  recurrence — NCAAF DUPCTX reads 0.

## Scope when found

- 52 orphan rows (gid date ≠ game_date) out of 317 total
- 13 active duplicate matchups; 2 with conflicting tier, 4 with
  conflicting pick side (one game had `rl/HOME` vs `rl/AWAY`)
- **NCAAF-only.** NFL 0 mismatches, MLB 1, NBA/NHL/NCAAB 0.
- 51 of 52 orphans referenced by `jerry_reads` — deletes must repoint

## Real-world damage

2026-09-18 Sharp Card published **Oregon -56.5 STRONG/77** sourced from
a 9/15 row, while the live row said **-58.5 LEAN/62**. The composer
sorts by conviction, so a stale-but-higher-conviction row beat the
fresh read. Users saw a 3-day-old line and a tier that no longer
reflected current data.

## Known residual

Houston @ Texas Tech appears on two **adjacent game_dates** (9/18 and
9/19). Two independent pulls put it 9/18 at -7.5; one lone row says
9/19 at -14.0. Both surviving rows are internally consistent so they
pass the new constraints. Deciding which date is real is a
schedule-truth question — confirm the actual kickoff, then delete the
phantom.

Related: [[feedback-fix-at-root-three-parts]],
[[project-close-spread-sign-bug-914]],
[[feedback-college-sports-no-props]]
