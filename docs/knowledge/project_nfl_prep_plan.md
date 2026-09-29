---
name: NFL Phase 1 prep plan (queued 2026-05-07)
description: NFL pipeline build kicking off morning of 2026-05-07; targets Sept Week 1 launch
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
NFL Phase 1 build queued to start morning of 2026-05-07 with last-season data pull as Day 1 task.

**Why:** NFL is the biggest betting market and the largest in-season revenue lever. Target launch Week 1 (Sept 4 2026). 4 months runway means data pull + schema + pipeline + props need to start now to leave Aug for preseason calibration soft-launch.

**How to apply:** Tomorrow's session opens with this task. Sequence:
1. Verify `nfl_data_py` package works locally — pulls nflfastR play-by-play data, free, comprehensive. Test on 2025 season schedule + one game PBP.
2. Schema migration: `nfl_team_stats`, `nfl_player_stats`, `nfl_game_results`, `nfl_props`, `nfl_weekly_cards` (mirrors NBA + UFC patterns we've shipped).
3. Backfill 2025 season — full season just ended Feb 2026, fresh complete data available. Backfill into `nfl_game_results` for audit calibration baseline.
4. Build `nfl_pipeline.py` for team-level stats (offense/defense EPA, red zone eff, third down, pace equivalent).
5. Cron cadence — weekly Tuesday during build (matches existing MLB cron infrastructure), shifts to Tue/Thu/Sat/Sun in-season Sept onward.

Phase 2 (June-July): props pipeline (passing/rushing/receiving yards, anytime TD), weekly slate generator, app surface.
Phase 3 (Aug): preseason soft-launch + calibration cohort building.
Phase 4 (Sept Week 1): public release.

Key constraint: NFL is weekly (Thu/Sun/Mon games), not daily — generator drops Thursday 8am ET, refreshes Fri/Sat/Sun.
