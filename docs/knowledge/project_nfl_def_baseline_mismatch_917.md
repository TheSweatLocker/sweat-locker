---
name: project-nfl-def-baseline-mismatch-917
description: "NFL prop \"vs baseline\" percentages nonsensical (712% etc) — team-total defense stat compared to player-level league_baseline. Text stripped as hotfix 9/17; proper fix requires per-family team_league_baseline field"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-18T00:11:34.411Z
---

**Symptom (Andy 9/17 spot-check):** Gibbs O3.5 REC Jerry read shows:
> "Opp BUF allowed 26.0 Receptions/game L5 (**+712%** vs baseline) — soft matchup"

**Root cause:** [nfl_generate_props.py:1147-1167] compares TEAM-total yielded per game (from `fetch_nfl_defense_recent_allowed`, which sums stat_col across all opposing players who played opp_team) to `cfg['league_baseline']` — which is player-level for some families and team-level for others:

| Family | league_baseline | Scale | Comparison valid? |
|---|---|---|---|
| pass_yds | 235.0 | TEAM ✓ | valid |
| pass_tds | 1.4 | TEAM ✓ | valid |
| receptions | 3.2 | PLAYER ✗ | **BROKEN** |
| reception_yds | 42.0 | PLAYER ✗ | **BROKEN** |
| rush_yds | 55.0 | PLAYER ✗ | **BROKEN** |
| rush_attempts | 13.0 | PLAYER ✗ | **BROKEN** |
| longest_reception | 21.0 | PLAYER ✗ | **BROKEN** |

Team-total = ~26 receptions/game, player-baseline = 3.2 → 712% delta. Nonsense.

**Hotfix (9/17)**: dropped the `(+N% vs baseline)` phrase from the user-visible signal text on this line. Direction (soft/stout tier bonus) still fires correctly since the delta test still evaluates. Users just don't see the bogus %.

**Proper fix (v1.0.2)**:
1. Add `team_league_baseline` field per family config — team-total avg allowed per game (~25 for receptions, ~230 for reception_yds, ~110 for rush_yds, etc)
2. Use `team_league_baseline` in the def-vs-league comparison
3. Restore the "+N% vs baseline" text with valid math
4. Regen existing prop_jerry_reads to overwrite the current stripped versions

**Files touched (hotfix)**: `mlb_pipeline/nfl_generate_props.py:1147-1178`. Signals `def_recent_soft` / `def_recent_stout`.

**Not yet done**: existing prop_jerry_reads generated BEFORE tonight's fix still show the "+712%" text baked into their long_read. Regen or leave as-is if the text is rare enough not to matter.
