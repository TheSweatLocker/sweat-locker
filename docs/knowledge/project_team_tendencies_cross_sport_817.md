---
name: project-team-tendencies-cross-sport-817
description: "Port ATS/OU/ML L10 team tendencies from MLB to every team sport (NFL, NCAAF, NCAAB, NHL) — signals + backfill + wiring"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-17T18:11:34.957Z
---

MLB shipped 2026-08-17: 10 team_form signals + `mlb_game_context` columns
(home/away_ats_last10, home/away_ou_last10_overs/unders, covers_as_fav/dog%,
ml_last10) populated nightly by `backfill_team_tendencies.py`. Fires 14
chips/day across 9/11 games at DISCOVERY weight; will auto-promote to
VALIDATED as hit-rate data accrues.

**Port to every team sport:**
- NFL — ✅ **shipped 2026-08-17** — L4 window (17-game season):
  `20260817_nfl_team_tendencies.sql` + `20260817_nfl_team_form_signals.sql`
  + `backfill_nfl_team_tendencies.py` (wired into nfl_pipeline.yml).
  Smoke-tested: 285 games / 32 teams; SEA/NO/MIN all 4-0 ATS L4;
  DAL/GB/ARI/NYJ/TB all 0-4 ATS L4; DEN 83% covers-as-fav.
- NCAAF — ✅ **shipped 2026-08-17** — L5 window:
  `20260817_ncaaf_team_tendencies.sql` + `20260817_ncaaf_team_form_signals.sql`
  + `backfill_ncaaf_team_tendencies.py` (wired into ncaaf_pipeline.yml).
  Smoke-tested against 3,831 games / 699 teams.
- NCAAB — `ncaab_game_context` + backfill (L10 works, big season)
- NHL — `nhl_game_context` + backfill (L10 works, 82-game season)
- UFC — N/A (individual, not team)

**Per-sport work per port (~2hrs each):**
1. Migration: add 17 tendency columns to `{sport}_game_context`
2. Copy backfill_team_tendencies.py → sport-specific version reading
   from `{sport}_game_results` + writing to `{sport}_game_context`
3. Seed signal_sources rows for the sport (copy MLB's 10 team_form
   entries, adjust `sport` field + window lengths per season size)
4. Wire backfill into sport's daily workflow (after resolver, before
   enrich)

**Why priority:** signals validated in one sport (once MLB hit-rates
land) can inform which ones to seed first in others (skip ones that
underperform, boost ones that hit >55%).

**How to apply:** Whenever we open a sport-specific workstream (NFL
launch, NCAAB launch, etc.) before season starts, this port is on the
list. Don't backfill live during season — retro-backfill 30d then start
seeding forward.

Related: [[project-jerry-vs-sharp-card-817]],
[[project-cohort-engine-universal-architecture]],
[[project-cross-sport-quality-gates-810]]
