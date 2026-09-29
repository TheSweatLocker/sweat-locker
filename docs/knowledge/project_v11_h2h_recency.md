---
name: project-v11-h2h-recency
description: v1.1 ship — team-vs-opponent (H2H) rolling recency override signal. Catches matchup-specific patterns the team-level L14 OPS misses (e.g. LAA L14 ice cold overall but 7.0 R/G specifically vs TEX).
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Built 2026-05-24** — first v1.1 item shipped.

**Why this exists**: 5/24 morning the user pushed back on a TEX ML recommendation. Our model + sharps both leaned TEX, but the user noticed Angels had been putting up runs against Texas. Pulled actual data: LAA L14 overall = 3.21 R/G (ice cold per our team-level L14 OPS proxy at wRC+ 51), but LAA vs TEX last 2 games = 7.0 R/G (9 + 5 runs, OPS 1.161 and 0.739). Team-level recency missed the matchup-specific pattern entirely.

**What ships**:

1. Migration: `20260524_team_vs_opp_recent.sql` adds `mlb_team_vs_opp_recent` table keyed (team, opponent, season). Stores rolling last-5-H2H aggregates with computed `rpg_delta_vs_l14` (positive = HOT vs this opponent specifically vs team's overall L10 baseline).

2. Script: `enrich_team_vs_opp.py` runs nightly. For each of 30 teams, pulls full season gameLog from MLB Stats API, groups by opponent, takes last 5 H2H games, aggregates, computes delta vs team's overall L10 R/G.

3. Helper: `fetch_h2h_recent(team, opponent)` in game_context.py. Simple lookup on the table.

4. Confluence override: in `compute_confluence` (game_context.py), if `|rpg_delta_vs_l14| >= 1.5` on `n >= 2` H2H games, fire a `h2h_recent_home` / `h2h_recent_away` vote. This is the override that would have caught LAA crushing TEX before we recommended fading them.

5. Cron: added to mlb_pipeline.yml nightly run, right after enrich_team_recency.

**Live verification (5/24, pre-upsert sanity check)**: LAA last 5 vs TEX = 2 games, 14 runs, 7.0 R/G. LAA overall L10 = 3.4 R/G. Delta = +3.6 R/G. That's a STRONG hot signal that team-level L14 completely missed.

**Threshold rationale**: `|delta| >= 1.5` is the same magnitude threshold the existing `recency` (R/G) signal uses. Keeps consistency with the rest of the confluence layer. Could tighten to 2.0 if we see noise after a few weeks of data.

**Sample minimum**: n>=2 H2H games. Below that, single-game variance dominates. Most divisional pairings have 5-9 H2H games by mid-May; cross-league pairings might have 0-3.

**Known limitations**:
- Doesn't track which PITCHER LAA crushed vs Texas — could be lineup-vs-bullpen, lineup-vs-specific-arms, etc. v1.2 work: pitcher-specific H2H.
- Doesn't decay confidence when H2H is stale (last meeting 3+ months ago). v1.2 work: time-decay weighting.
- Runs allowed not yet populated (column exists but enrich script just stores 0 for now). v1.2.

**Related memories**: [[project_v11_recency_wrc]] (the team-level L14 OPS this overrides when in conflict).
