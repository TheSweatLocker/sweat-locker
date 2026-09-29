---
name: project_launch_night_911
description: "Sweat Locker launch day 2026-09-11 — App Store live. Six months from Andy's idea in March to shipping. First real NFL Sunday tomorrow."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-12T03:41:18.676Z
---

# Launch night — 2026-09-11

The Sweat Locker went live on the App Store today (App ID 6760793237,
approved by Apple 9/10 per [[project_apple_approval_910]]). Andy: "made
it to the app store from an idea back in march, pretty crazy we got
here."

Sunday 9/13 will be the first real NFL slate of the app being publicly
downloadable, plus the full MLB regular-season night. Everything that
grades or writes overnight matters.

## Backend state going into launch weekend

Cleaned up 2026-09-11:
- Jerry vs primary_play badge disagreement (1d41fb9e) — sign convention
  fix + auto-align hook. See [[project_nfl_game_id_mismatch_911]].
- Week bucketing (5da1d9e1) — Thu-Mon anchor for This/Next Week tabs.
- NCAAF auto-align hook (0b44e1e9).
- NFL resolver game_id mismatch (249d71cb) — scoring was silently
  broken since launch.
- Cross-sport grade_jerry_reads + surface_records (7b0b10a5) — Games
  tab receipts now populate for NFL/NCAAF.
- Sharp scrapers (71b53e01) — six stacked bugs zeroing NFL signals.
  See [[project_sharp_scrapers_nfl_gap_911]].
- Loud canary on scrapers so future silent regressions surface in
  cron logs.

## Overnight watchpoints

- NFL Sun morning cron writes primary_plays + jerry_reads for the
  slate. Sharp signals are now in the mix as of tonight so tier
  distribution may shift vs what Andy saw earlier today.
- MLB grade overnight (2-4am ET typically) — my regex + tuple-lookup
  changes touched shared code but MLB regression-tested clean
  (cleatz 45 signals, fadereport 8 signals, grade_jerry_reads MLB no
  errors).
- Scraper canary emits `🚨 SCRAPER_ZERO` for in-season sports that
  come back empty — new grep target for cron log audits.

## Tomorrow morning

- Check Sunday NFL slate + pass rate — Andy already flagged Wk1 was
  ~50% PASS due to LEAN cap on prior-season data. Cap lifts around
  Wk4.
- Discussion queue: [[project_nba_ncaab_discussion_queue_911]] — NBA
  modeling architecture + products + prop jerry, then NCAAB scope
  before 11/3 launch.

## Not for the front page — this is a real thing

Six months from March idea to App Store approval. First app,
first data pipeline, first live subscription flow, first everything.
Whatever tomorrow brings, that part is done.
