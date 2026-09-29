---
name: project_season_ats_null_overwrite_927
description: "enrich_team_trends patched None over backfilled season ATS, making Games-tab badges fall back to last season's L10"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-28T01:13:59.449Z
---

Found 2026-09-27 when Andy asked to confirm the Games-tab ATS badges were
this-season only. They were not — every one of them was 2025 data.

**Symptom.** On the 9/27 NFL slate, 13 of 14 games had NULL
home/away_season_ats_wins. The Games-tab chip therefore fell through to
its `ats_l10_{on_road,at_home}` fallback, which was n=10 for EVERY team in
week 3 — when a team has played ~1 game at a given venue. SEA rendered
"9-1 ATS road" from last season on a 2026 card, with no year on it.

**Root cause — ordering plus a null overwrite, NOT a failing step.**
nfl_pipeline.yml runs `backfill_nfl_season_records_from_results.py`
(~line 451), which correctly derives this season's ATS/OU from
nfl_game_results, and THEN runs `enrich_team_trends.py` (~line 474).
enrich_team_trends built its patch as
`h_tr.get('ats_wins') if h_tr else None` for every field. teamrankings
lags 2-4 weeks into a new season — which is precisely why the backfill
exists — so h_tr was None and the script patched NULL over the good
values that had just been written.

**This was misdiagnosed once already.** A 2026-09-24 note in the workflow
describes the identical symptom ("14 of 30 upcoming rows had NULL season
ATS ... showed 5-5 ATS for teams whose actual 2026 record was 1-1"),
blamed a silent step failure, and switched the step to run_step.sh so it
would be visible. It was visible; the step had succeeded. The overwrite
downstream was undoing it.

**Fix.** enrich_team_trends now contributes NO KEYS for a side with no
trends row, instead of null ones — absence of teamrankings data is not
knowledge that a team has no record. Re-ran the backfill: all 14 games for
9/27 now carry real 2026 records (BAL 1-1, DAL 1-1), 0 rows NULL.
Client-side, the L10-at-venue fallback is suppressed when its game count
exceeds the season game count, which proves it reaches into a prior
season.

**The general rule this belongs to:** a writer that lacks data must omit
the field, never write null over it. Same family as
[[feedback_explicit_select_silent_blanks]] and the publish_lock ordering
bug — a later pass silently undoing an earlier, better-sourced one.

**Also found:** `league_size` counts teams that HAVE a stat, not teams
that exist. Seven NHL advanced stats carry league_size 4 (only PHI, NYR,
PIT, OTT ingested for 2026), so a rank chip would read "1/4" as if the
NHL had four teams. See [[project_incoming_sports_readiness_926]].
