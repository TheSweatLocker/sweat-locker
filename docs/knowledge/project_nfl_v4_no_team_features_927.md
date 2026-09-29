---
name: project_nfl_v4_no_team_features_927
description: "NFL v4 spread model has zero team-strength features, so it outputs a near-constant always-HOME +1.5; also dead since 9/21"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-27T22:19:28.778Z
---

Found 2026-09-27 while testing whether the SP+ compression finding
([[project_sp_plus_compression_927]]) also applies to NFL.

**v4_spread is a stuck constant.** Across the 29 NFL context rows that
have a value:
  v4_spread  min +1.37  max +1.99  sd 0.197   -- ALWAYS POSITIVE (home)
  v3_spread  min -15.74 max +11.43 sd 4.795   -- on the same rows
  v4_confidence = 0.311 on EVERY row
Worst example: CHI@CAR closed -21.5, v3 said -15.74, v4 said +1.44.

**Root cause: v4_features_used contains no team-strength feature at all.**
  temp, week, wind, season, is_dome, away_bye, div_game, home_bye,
  away_rest, home_rest, is_playoff, away_short_week, home_short_week
No EPA, no madden, no power_diff, no offense/defense rating, no team
identity. The model predicts margin from weather + rest + week number, so
~+1.5 is simply league-average home-field advantage -- the only signal
those inputs carry. v4_total behaves the same (45.37 / 47.73, dome-split).
The model file itself is fine (models/nfl_v4_spread.pkl, 2026-09-22); the
FEATURE BUILDER is what fails to pass team stats.

`away_bye: 1` AND `home_bye: 1` on the same week-2 game is also wrong.

**It is also dead.** v4_spread is populated on only 29 of 316 rows, on 5
dates (9/13, 9/14, 9/17, 9/20, 9/21), and NULL since 9/21 -- 6 days.

**Why this matters.** v4 is consumed as a model lens: nfl_game_context.py
~969-997 derives `v4_side = 'HOME' if v4_spread > 0 else 'AWAY'` (so always
HOME when present) and feeds `'v4': _side_of(v4_spread)` into the consensus
structure; game_context.py ~2790-2823 gates tier on "Jerry+v4 split
direction". So a no-information constant was voting HOME in the lens count.
Note 20260926e made its prose MORE prominent ("V4 projects {v4_spread} vs
market {close_spread}") without anyone checking the number meant anything.

**Do not count v4 as a lens until the feature builder passes team stats.**
Same family as [[project_ncaaf_duplicate_total_lens_920]] (one opinion
counted twice) and the stale-shadow class in [[feedback_lr_daily_verify]]
(a model emitting one value for every game).
