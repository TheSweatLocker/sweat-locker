---
name: feedback-batter-hits-juice-trap-803
description: Batter Hits Over 0.5 at -200 or juicier is trap; PRIME hits rate is 69% (per prior audit)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-03T20:16:07.588Z
---

**Rule**: Batter Hits Over 0.5 props at -200 or juicier are traps. Skip regardless of conviction tier.

**Why**:
- [[project_batter_hits_signal_712]] audit: PRIME-tier batter hits OVER hits at ~69% historically
- Break-even at -200 = 66.7%. At -250 = 71.4%. At -300 = 75%. At -350 = 77.8%.
- **Any Hits Over 0.5 priced juicier than -186 (65% implied) starts eating our audit edge**
- Star hitters (Peña, Alvarez, Altuve, Ohtani) routinely priced -300 to -400 by ESPN BET / FanDuel for hits O 0.5
- Even PRIME 100 conviction ≠ 78% hit rate — regression to the mean kicks in on 7-for-7 hot streaks

**Real trap examples**:
- 8/3 Jeremy Peña O 0.5 @ -350 (77.8% implied) — PRIME 100 conv on 7-for-7 L7. Historical PRIME = 69% = -8.8pp EV, ~-8% ROI
- 8/3 Yordan Alvarez O 0.5 @ -320 (76.2% implied) — same trap
- Rule of thumb: if you see -300+ on batter hits O 0.5, skip the star and take a middle-of-order teammate at -110 to -140

**How to apply**:
- If pipeline says PRIME 90+ conv batter hits over and price is -200+, look for a **teammate in the same lineup** with same signal at fair price
- Correlated bet on the same offensive matchup (opp starter xERA + team L14 wRC+) can be captured cheaper via the #4-6 hitters vs the #1-3 star
- Example: Peña O 0.5 @ -350 (TRAP) → Trammell O 0.5 @ -128 (FAIR); same lineup, same Bieber matchup, PRIME 92 conv, +12.9pp edge
- Alternatively: take the star at higher line (O 1.5 or O 2.5) at plus-money — Peña 7-for-7 L7 supports 2-hit game at +200 to +650 pricing

**System gap this rule exposes**:
- `sweep_prop_coverage.py` MARKET_MAP does NOT include `batter_hits`, `batter_home_runs`, `batter_total_bases` markets — we ingest pitcher props but skip hitter props on the coverage side
- Result: `mlb_pipeline_props` batter hits rows have `book_over_odds = None`
- Scoring engine can't factor juice → publishes traps
- Fix backlog: extend coverage sweeper to hitter markets so conviction tier can be juice-aware

Related: [[project_batter_hits_signal_712]] (parent audit), [[feedback_heavy_fav_ml_trap_803]] (same pattern on game-side), [[feedback_juice_fav_rl_trap_724]] (RL variant).
