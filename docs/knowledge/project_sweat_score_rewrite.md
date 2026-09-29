---
name: sweat-score-rewrite-5-16-distribution-fix
description: Game-level sweat score rewritten 2026-05-16. Old formula clustered all games at 42-45 PASS; new formula spreads across 40-90 with PRIME/STRONG/LIGHT_LEAN/PASS gradient.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## What broke

Old `score_mlb_game` in `play_of_day.py` had:
- Base 30 + only NRFI/xERA/spread bonuses
- No factor for confluence, 1st-inn extremes, pitcher mastery, or PRIME prop stacks
- Most games landed 42-45 PASS because none of the active signals stacked to clear STRONG (62)

Also: server's `sweat_tier_for` used **68/62/<62** thresholds while app's `getSweatTier` used **80/65/50/<50**. Two sources of truth. Server tier string was technically written but app re-derived from the numeric score using its own bins, so users saw the 80-PRIME / 50-PASS world while server thought it was 68/62.

## What was fixed

**`mlb_pipeline/play_of_day.py`:**
- `score_mlb_game` rewritten to include:
  - NRFI band (audit-calibrated, 90-94 sweet spot still +30)
  - xERA gap + both-elite-arms
  - 1st-inning extremes (fragile YRFI or lockdown NRFI)
  - **Signal confluence (NEW)** — net magnitude ≥4 = +10, ≥5 = +14
  - Spread delta + total delta
  - K gap
  - **Pitcher mastery / anti-mastery vs current opp (NEW)** — ≤2.5 or ≥7.0 ERA = +5
  - Park + cold + wind
  - **PRIME prop stack count (NEW)** — 4+ PRIME = +20 (stack-alert signal)
- `sweat_tier_for` aligned to app's 4-tier system: **≥80 PRIME / ≥65 STRONG / ≥50 LIGHT_LEAN / <50 PASS**
- `run()` now pre-fetches today's props once and passes per-game lists into `score_mlb_game`

## Distribution validation (2026-05-16 slate, 15 games)

```
PRIME (≥80):       2 games (TOR/DET 85 POTD, BOS/ATL 81 stack alert)
STRONG (65-79):    5 games (MIA/TB, PHI/PIT, NYY/NYM, BAL/WSH DOD, TEX/HOU)
LIGHT_LEAN (50-64): 5 games (SD/SEA, KC/STL, MIL/MIN, ARI/COL, LAD/LAA)
PASS (<50):        3 games (CIN/CLE, SF/ATH, CHC/CWS)
```

Target distribution per slate going forward:
- 1-3 PRIME (POTD + stack-alert games)
- 3-6 STRONG (confluence + DOD + multi-PRIME-prop)
- 4-7 LIGHT_LEAN (single-signal or single-PRIME-prop)
- 1-3 PASS (no edges)

## Audit items for 5/17

1. **Validate the prop-stack-count bonus didn't over-promote BOS/ATL-style stacks.** BOS/ATL with 4 PRIME hits-unders + 2 PRIME pitcher props (Elder HA + Tolle Ks) landed at 81. Grade vs result — was the stack-alert game actually a play worth top tier?

2. **Validate confluence weights.** PRIME +4 confluence games audited at 68.8% STD; my formula gives them +10 conviction. After a week of resolved games, check whether PRIME-confluence games systematically clear STRONG (65+) as expected.

3. **1st-inning fragile bonus** (Bassitt 6.43 → +5, Teng 9.0 → +8). Did games with these signals correctly land STRONG/PRIME?

4. **The +20 stack alert ceiling.** With 4+ PRIME props in one game, the bonus alone clears 50 → 70. Combined with NRFI + xERA gap could over-promote. If receipts show stack-alert games miss at >40%, dial back to +15.

## Files touched 5/16

- `mlb_pipeline/play_of_day.py` — full rewrite of `score_mlb_game`, threshold realignment, prop pre-fetch in `run()`

App side did not need a change — `getSweatTier(score)` in `app/index.tsx:2497` already used 80/65/50 thresholds; aligning the server tier strings to match closed the loop.

## Related
- [[project_dod_confluence_bug.md]] — same-day fix for DOD direction logic; both were "scorer doesn't reflect what app surfaces" bugs
- [[project_may15_calibration_notes.md]] — 5/17 audit docket
