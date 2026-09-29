---
name: jerry-read-phase-a-b-605
description: "6/5 Jerry read upgrade — Season Snapshot (Phase A) + career SP + team pitching staff (Phase B). In-memory fetchers, no migrations."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Shipped 6/5 in two commits (`62e9a56` Phase A, `e796482` Phase B). Triggered by Doc Sports comparison — user wanted that depth of context, our analytical layer kept.

**Phase A — team_snapshot block + Season Snapshot section**
- `fetch_team_snapshots()` reads existing `mlb_team_offense` + `mlb_bullpen_stats` tables, computes league ranks on 8 metrics (R/G, OPS, wRC+, xwOBA, BP ERA, save%, holds, OAA). Cached per process.
- `_team_snapshot_block(team_name)` builds the per-team dict, wired into `team_snapshot.{home,away}` in struct.
- Prompt template MLB row gained `**Season Snapshot**` section between Setup and Pitcher Matchup. Version 1 → 2.

**Phase B — career SP + full-staff team pitching**
- `fetch_team_pitching_snapshots()` pulls full-roster team pitching from MLB Stats API (`/teams/{id}/stats?group=pitching`) for all 30 teams, computes ERA/WHIP/BAA/K-BB ranks. ~3s, 30 calls per cron tick.
- `fetch_career_sp_stats(name)` does search → playerId → `/people/{id}/stats?stats=career&group=pitching`. ~15 starters × 2 calls per tick.
- Both wired into existing struct: `team_snapshot.{home,away}` gains `team_era`/`team_whip`/`team_k_bb`/`team_baa` + ranks; `pitchers.{home,away}.career` gains W-L, ERA, WHIP, BAA, K, BB, IP, BF.
- Prompt template MLB row version 2 → 3 with: expanded Snapshot section (two-half format separated by `‖`: offense ‖ pitching) and new `CAREER SP RULES` block (parenthetical anchor, >0.15 WHIP delta = regression flag, <300 IP = thin sample explicit caveat).

**Why no migrations:** Career stats only need refresh ~daily and live-fetching from MLB Stats API is 3 seconds per cron — cheaper than the schema migration + backfill complexity. If we ever need to surface them outside Jerry reads (audit grading, etc.), revisit with a `mlb_pitcher_career_stats` table.

**How to apply:** When debugging Jerry reads with apparent missing depth, check that `team_snapshot.*.team_era` and `pitchers.*.career` are populated in the struct. If both are None, the MLB Stats API call is failing (rate limit / network). If only career is None, the name search missed — check for accent stripping / suffix-mismatch (e.g., "Martín Pérez" might miss as "Martin Perez").

**Phase C ideas (not queued, no deadline):**
- Reliever K rate vs opp lineup K rate (current bullpen depth doesn't account for matchup)
- Catcher framing rank by team (we have `catcher_framing` per-game but no team aggregate)
- Defensive split: infield UZR vs outfield UZR (for groundball vs flyball SP matchups)

Linked: [[feedback-backside-dictates-app-renders]] (server-side rendering rule that drove the in-memory fetcher approach), [[feedback-verify-pitcher-attribution]] (K-gap interpretation finally fixed in v3 prompt).
