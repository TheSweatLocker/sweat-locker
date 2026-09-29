---
name: project_nfl_sota_signal_audit_921
description: "2026-09-21 audit of Andy's NFL \"state of the art\" ideas — primetime splits, QB-vs-bad-pass-D, coach analysis. Two rejected on evidence, one reframed. Plus what nfl_game_results actually holds."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T21:13:05.409Z
---

Andy (2026-09-21): "Are we able to track QBs record in primetime, team
records in primetime, QB stats against bottom tier pass defenses? Thoughts
on coach analysis? If ncaaf and nfl are going to be in spotlight they need
to be state of the art."

## What the data actually holds (better than expected)

`nfl_game_results` (1,967 games, 2020-2027) already carries:
`home_coach`/`away_coach`, `home_qb_name`/`home_qb_id`, `gametime`,
`weekday`, `referee`, `roof`, `surface`, `stadium`, `temp`, `wind`,
`home_rest`/`away_rest`, `div_game`, plus pre-graded `spread_result` /
`total_result` / `run_line_result`. So all four ideas were buildable
immediately — no new scraping needed.

`nfl_player_stats` (38,441 rows, 2022-2026) has per-player per-week
passing_epa / yards / attempts / TDs with `opponent_team`. Joins to
results via season+week+team.

`ncaaf_game_results` (15,481) has `kickoff_utc`, `neutral_site`,
`conference_game`, `attendance`, `temp` — but NO coach and NO QB fields.
So NCAAF primetime is computable; NCAAF QB/coach work is not, yet.

## VERDICT 1 — primetime: REJECTED, do not build

No slot-level effect. Home ATS by slot: TNF 46.7% [37-57], SNF 53.8%
[44-63], MNF 48.6% [40-58], Sun-early 49.4%. Every CI contains 50.

Per-entity splits look seductive and are noise. QB primetime-vs-other ATS
delta showed excess variance (SD 16.8pp vs 12.5pp expected by chance), but
the decisive out-of-sample test kills it — correlation between a QB's
2020-23 primetime delta and their 2024-27 delta:

    QB    r=+0.130 (n=16)
    TEAM  r=+0.146 (n=22)
    COACH r=+0.170 (n=17)

Coaches who UNDER-performed early went +3.2pp later — mean reversion, the
opposite of fading them. "Tom Brady 2-12 ATS in primetime" is true and
worthless. Classic narrative-stat trap: looks authoritative on a card,
degrades picks. QB/team/coach are also badly confounded (LaFleur = GB =
Rodgers; Bowles = TB = Brady) so the QB "signal" is likely the same
variance counted three times.

Power caveat: 8 seasons, 16-22 entities. Rules out a large effect, not a
tiny one.

## VERDICT 2 — QB vs bottom-tier pass D: REAL but NOT PROJECTABLE

The contemporaneous effect is large and cleanly monotone (2,322 QB games,
>=10 att):

    opponent pass D    yds/att   EPA/att   pass yds   pass TD
    TOP-8                 6.58    -0.110      204.1      1.17
    middle                7.15    +0.019      220.9      1.34
    BOTTOM-8              7.60    +0.140      238.5      1.60

+34 pass yards and +0.43 TD vs bottom-8 — worth real money on a prop line.

BUT you cannot know the tier in advance:
  * year-over-year pass-D rank correlation: **r = -0.000** (n=96 team-seasons)
  * within-season, rank through week W vs rest-of-season:
    wk1-3 r=+0.045, wk1-5 +0.175, wk1-10 +0.188 — never stabilises.

So prior-season "bottom-tier pass defense" is worthless as a forward
signal, and even mid-season rank is weak. Use current-season only,
recency-weighted, shrunk HARD toward league mean, and assume the market
has most of it. Caveat: our metric is passing-EPA-per-attempt allowed over
~8-17 QB games/team-season, itself noisy, which attenuates r.

## VERDICT 3 — coach analysis: no ATS edge, but wrong question

Coach ATS primetime deltas don't persist (above). Coach identity is
probably still useful via *tendencies* that do persist — 4th-down
aggression, pace/seconds-per-play, run-pass rate, timeout usage — which
feed totals rather than sides. Untested as of 2026-09-21; that is the
version worth building, not coach W-L.

## What DID survive: see [[project_nfl_wind_under_edge_921]]

Structural factors tested at the same time (rest advantage, roof,
divisional, temperature, referee) were all null — every CI contained 50%.
Only moderate wind cleared.

Related: [[project_signal_framework_821]], [[project_ats_streak_patterns_902]]
(same evidence-based rejection pattern), [[project_nfl_sota_rebuild_917]].
