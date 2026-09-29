---
name: project_incoming_sports_readiness_926
description: NBA opens 2026-10-03 with a season-format mismatch that 400s the Team Stats query on every card; NCAAB has nothing built
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-27T01:45:19.060Z
---

Measured 2026-09-26, one week before NBA opening night (first game
2026-10-03, Miami @ Toronto).

| sport | ctx | picks | team stats | sos/sor | graded receipts | jerry | externals | signal recs |
|---|---|---|---|---|---|---|---|---|
| NFL   | 316 | 223 | 576 (2026) | 32  | 935  | 80  | 759  | 7 |
| NCAAF | 379 | 89  | 3314 (2026)| 152 | 203  | 216 | 1136 | 8 |
| MLB   | 205 | 22  | 630 (2026) | 30  | 7050 | 619 | 8849 | 3 |
| NHL   | 59  | 22  | **92** (2026, ~2.9/team) | 32 | 32 | 37 | **0** | **0** |
| NBA   | 81  | 76  | **600 but 2024/2025 only** | **0** | 2 | **0** | **0** | **0** |
| NCAAB | **0** | **0** | **4523 all season 2024** | **0** | 0 | 0 | 0 | **0** |

**NBA season format — checked, NOT a crash.** `team_stats_rolling.season`
is an INTEGER and `nba_game_context.season` is the STRING `'2026-27'`, so
a raw pass-through 400s:

    GET team_stats_rolling?sport=eq.NBA&season=eq.2026-27
    -> 400  invalid input syntax for type integer: "2026-27"

But the app never passes it raw. All three consumers do
`Number(season) || new Date().getFullYear()`, and Number('2026-27') is
NaN, so they query 2026 — the correct integer, since the matview keys
'2025-26' as 2025 (first year). RecentScheduleCard only season-filters
NFL/NCAAF at all. **I claimed on 2026-09-26 that every NBA card would
400 and that was wrong** — verified by reading all four call sites.

The REAL NBA gap is data, not a crash: 0 rows in team_stats_rolling for
season 2026 (the 600 that exist are 2024/2025), so Team Stats renders
EMPTY rather than broken.

NBA is already generating 76 picks with none of the supporting surface
(no stats, no SOS/SOR, no Jerry read, no externals, no signal records),
so a user tapping an NBA pick sees a pick and almost nothing else.

NCAAB (opens early Nov) has no game context whatsoever and its 4,523
stat rows are two seasons stale.

NHL is live and functioning — picks, grading and Jerry all flow — but
its stats are very thin (2.9 keys per team against NCAAF's 22) and it
has no externals and no signal track records, so money-flow and
signal-record surfaces are empty.

Also found: `jerry_cache.sport` holds BOTH 'MLB' and 'mlb'. Any exact-match
filter silently under-counts — it fooled this audit before pagination and
case were accounted for.

Related: [[project_ncaab_nba_readiness_922]], [[project_all_sports_readiness_820]].
