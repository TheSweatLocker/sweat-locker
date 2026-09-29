---
name: project-team-stats-weekly-blend-909
description: "🎯 9/8 queued: after game 1, prior-season stats should be linked as baseline; game 1 = your average; blend weekly forward"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T18:25:06.420Z
---

**User directive · 9/8 evening:** "After one game I think all previous season stats should be linked and game one be your averages then update weekly. Make sense?"

## Current state (2026-09-09)

`nfl_game_context.py` load_team_stats_with_fallback:
- Loads current season stats
- If current season is empty (preseason), falls back to prior season shrunk to league mean
- Blends per-game rates then re-scales to season totals

**Problem:** after Week 1 you have ONE game of "current" data. That single game overwrites the prior-season signal completely (or gets diluted with league mean when the games count is low). Neither is right.

## User's proposed model

1. **Preseason:** show prior-season stats as-is (they're the best signal)
2. **After Game 1:** blend = (prior season × weight_prior) + (current game × weight_current)
3. **Weekly updates:** as games accumulate, current-season weight increases
4. **Week 6-8ish:** prior season weight approaches zero, current season dominates

## Implementation questions to answer

1. **Blend curve** — linear (1/16, 2/16, 3/16 ...) or bayesian (regularize to prior with shrinkage factor tied to games_played)?
2. **Which stats blend, which don't** — offense/defense per-game rates yes; roster/injury signals no (they're current-only)
3. **Signal freshness display** — show user "based on 1 game + prior season" for transparency
4. **What about NCAAF** — 12-game season means the blend curve should be different than NFL 17-game
5. **What about NBA/NHL** — 82 games means Week 1 stats stabilize fast; blend period much shorter
6. **What about MLB** — 162 games means signal stability comes early; probably need this least

## Where this touches code

- `nfl_game_context.py` load_team_stats_with_fallback + BLEND_MIN_GAMES = 4
- `ncaaf_game_context.py` similar structure
- `nba_game_context.py` — may or may not have prior-season logic yet
- Universal candidate: create `team_stats_blend.py` that takes (current_stats, prior_stats, games_played, sport) → blended stats

## Priority framing

- Not launch-blocking — NFL Week 1 was Thu 9/4, we're already past it
- **Highest value:** for NCAAF (which is week 2 already, small-sample games abound) and NFL Week 2+
- **Should be built before Week 2 kicks off** (Thu 9/11) if possible

## Related memory
- [[project_nfl_ncaaf_week1_readiness_820]] — self-heals by Wk 3-4 (this proposed fix would accelerate)
- [[project_data_infrastructure_priorities_908]] — foundation-first stack
- [[project_madden_top100_nfl_signal_824]] — Madden/Top-100 as static prior alongside
