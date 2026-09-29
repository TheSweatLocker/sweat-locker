---
name: NRFI model patterns from April data
description: Data-driven findings from 140+ resolved MLB games — temperature, wind, park, umpire signals for NRFI model calibration
type: project
---

NRFI model audit from 140+ resolved games (April 2026):

**Tier performance:**
- 88+ Lock: 64.0% (16-9) — only actionable tier
- 70-87: 55.8% (29-23) — no edge, downgraded to Neutral
- 55-69: 48.6% (18-19) — below base rate
- 41-54: 63.6% (14-8) — anomaly, mostly null xERA + cold weather games
- 40-: 25.0% (1-3) — YRFI confirmed

**Temperature (strongest signal):**
- ≤45°F: 79.2% NRFI (19-5) — boosted to +15 in model
- 46-55°F: 43.5% — WORSE than base rate, changed to -4 penalty
- 56-70°F: 41.2% — worst range, -3 penalty
- 71°F+: 59.3% — above base rate, +2 bonus

**Wind direction:**
- North: 81.8% (9-2) — 11 games, need more data
- SW: 41.2% (7-10) — YRFI lean
- Others: 54-58% neutral

**Park factor (totals):**
- Hitter parks 105+: 57.1% Over
- Pitcher parks 95-: 48.6% Over
- Neutral: 48.4% Over

**Dual elite pitchers (both xERA < 3.5):**
- 55.6% Over (15-12) — games still go Over despite elite starters

**Spread model: 37.8% accuracy (17-28) — disabled until May audit**
**Total OVER: 58.1% — active signal. UNDER: 48.3% — disabled**

**Why:** Calibrate model weights from real outcomes, not assumptions.
**How to apply:** Temperature weights updated in game_context.py. Revisit at 300+ games for wind/umpire signals. Re-enable spread after May 1 with correct wRC+ data.
