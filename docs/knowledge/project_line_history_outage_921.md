---
name: project_line_history_outage_921
description: "line_history died for NFL/NCAAF on 2026-09-09 and never existed for NHL/NBA/NCAAB. Fixed 9/21 by calling write_line_history_from_event from every odds puller. Remaining gap is CADENCE — non-MLB sports pull 1x/day vs MLB's 15min."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-22T00:38:35.506Z
---

Found 2026-09-21 while auditing NHL for the Oct 8 opener. Started as "NHL
has no line movement"; it was never an NHL problem.

## The outage

    sport    rows          newest BEFORE
    MLB      369,908       2026-09-21    ok
    NFL      397,022       2026-09-09    12 DAYS STALE
    NCAAF  1,475,950       2026-09-09    12 DAYS STALE
    NHL            0       never
    NBA            0       never
    NCAAB          0       never

`line_history` feeds detect_line_movement -> line_movement_flags ->
classify_line_moves and the Steam Room **Split view**. So football had NO
line-movement detection for twelve days OF THE NFL SEASON.

**This reframes [[project_fade_gate_performance_921]]**: the finding that
football classification sat at 5-9% was partly measuring a dead feed, and
the 51-NFL-flags-vs-1,378-MLB gap was never about football having fewer
games.

## Cause

`write_line_history.py` reads `odds_cache`, which is populated only when a
user opens the Games tab in the app. Newest `odds_games_*` row for ANY
sport is 2026-09-09.

MLB escaped because `line_poller.py` calls
`book_lines_writer.write_line_history_from_event` directly — a writer added
2026-09-11 whose own docstring says it exists "so line_history stays fresh
SERVER-SIDE and Steam Room's Split view stops depending on a user opening
the app". It was wired **MLB-only**, so every other sport kept depending on
exactly the thing it was built to remove.

`fix_steam_room_824.py` had already spotted the stale `odds_games_*` rows
back on 08-24 and written "Fix upstream: pull_odds.py or whatever writes
odds_games_MLB_*". Nobody did, for four weeks.

## Fix (commit dd124256)

Every sport's puller already holds the same slate response, so the call
went where it belongs: once in `odds_pull_core` (NHL, NBA), and in the
three standalone pullers (`nfl_`, `ncaaf_`, `ncaab_odds_pull.py`). Never
fatal — losing line_history must not cost the odds pull itself.

Verified live: NHL 782 rows / 33 events, NBA 810 / 41, NFL 964 / 15,
NCAAF 3,404 / 58. NCAAB wired, offseason. All newest 2026-09-22.

Immediately after: `detect_line_movement --sport NFL --lookback-hours 48`
produced **10 flags** where it had produced nothing.

## REMAINING GAP — cadence (not yet fixed)

`detect_line_movement --sport ALL` already supports all 7 sports and runs
in mlb_pipeline.yml at `--lookback-hours 6`. But movement detection needs
TWO snapshots inside the window, and outside MLB there is only ever one:

    MLB      mlb_line_poller.yml   every 15 min (~30 runs/day)
    NHL      nhl_pipeline.yml      1x/day  08:05 ET
    NBA      nba_pipeline.yml      1x/day  08:00 ET
    NFL      nfl_pipeline.yml      Tue/Wed/Wed/Thu
    NCAAF    ncaaf_pipeline.yml    Tue/Wed/Fri/Sat

Confirmed empirically: NHL yields 0 flags at both 6h and 48h because only
one snapshot exists. So day-over-day movement is now detectable for
football, but **steam / RLM / limit detection needs intraday polling** and
non-MLB sports cannot produce it at current cadence.

Options (Andy's call — recurring Odds API spend, though quota showed
4.88M remaining so cost is not the constraint):
  a. A shared multi-sport line poller on the mlb_line_poller pattern,
     every 15-30 min during active windows. Correct fix.
  b. Raise --lookback-hours for the thin sports. Cheap, but conflates
     24h drift with steam and would mislabel the classification.

Recommendation: (a).
