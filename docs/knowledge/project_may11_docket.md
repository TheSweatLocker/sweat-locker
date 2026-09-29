---
name: 2026-05-11 docket — carryover from massive 5/10 data day
description: Tomorrow's queue after shipping 9 audit cohort families + 4 pipeline/scout signals on 5/10. Includes ladder kickoff, NRFI×ump combo, lineup snapshot, RevenueCat, and queued v1.1 items.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
**Context:** 5/10 shipped 9 audit cohort families (K-Over alt-line, K-Over × total/bullpen correlation, Total × wRC+/xERA/BP/L5, Spread/ML × disparity, Pitcher rest, Umpire cross-cohorts) + 4 live pipeline/scout signals (v2 total ump filter, pitcher class projections, lineup wRC+ delta, ump scout display). Net effect: ~11 new audit dimensions + multiple live pick adjustments.

# 2026-05-11 Tomorrow's Docket

## 🎯 Morning audit + slate read (standard)
- Run morning audit on 5/10 slate results — first audit cycle to grade picks made with new ump filter, lineup degradation flag, class projections
- Validate: did POTD Braves/Dodgers UNDER 9.0 hit? (5-signal stack was the cleanest play). Did the new ump suppression catch anything? Did Cubs lineup degradation flag prevent a bad bet?
- Generate 5/11 picks via full cron

## 🪜 LADDER kickoff (if slate is solid)
- User decision yesterday: hold ladder for solid slate. Tomorrow morning, evaluate slate quality
- **Ladder criteria:** at least one PRIME-cohort pick with hit rate ≥65% AND sample ≥15
- Day 1 pick selected from highest-confluence stack (multi-signal alignment)
- Single pick only, no parlay. Public ladder thread starts.
- Format reference: see project_spread_ml_factor_cohorts.md for the structured ladder framework

## ⚡ Quick wins (queued — ship by EOD)

**1. NRFI 90-94 × NRFI-friendly ump combo cohort**
- Audit-only, ~45 min build
- Compute 2-factor combo: games where NRFI score is 90-94 AND assigned ump's nrfi_rate ≥ 0.55
- Hypothesis: stacks to 75%+ from baseline 71.4% (NRFI prime) and 60.7% (NRFI-friendly ump)
- File: extend `audit_tier_calibration.py` after existing umpire cohort block

**2. Snapshot `home_lineup_ops` / `away_lineup_ops` to `mlb_game_results` at resolve time**
- Currently transient on `mlb_game_context` (only 40 entries) — no historical audit possible
- Add columns via Supabase migration: `home_lineup_ops`, `away_lineup_ops`, `home_lineup_weight`, `away_lineup_weight`
- Update `resolve_game_results.py` to write these from current context at resolve time
- Enables future lineup-degradation audit cohort (forward-only after this ship)
- ~1 hour total (migration + code)

**3. Surface ump filter inside generate_props.py for K Overs (structured replacement)**
- Currently `'k-friendly' in ump_note` fuzzy text match gives +8 conviction
- Replace with structured lookup from `mlb_umpires.k_rate_above_avg`:
  - k_rate ≥ +0.2 (n≥30): +6 conviction
  - k_rate ≤ -0.2: -4 conviction (NEW — adds hostile fade)
- ~45 min, audit-anchored boost direction

## 🚀 Bigger swings (this week)

**4. Two-factor cohort family** — combos like `bp_high × hot_l5_cold`, `home_pen_advantage × short_rest_away_pitcher`. Build once individual buckets hit n≥80 (most are there).

**5. Out-of-the-box data ideas queued from yesterday's pitch:**
- Top-3 bullpen arm availability tracker (closer used yesterday = late-game vulnerability)
- Lineup vs L7 form (not season) as offense input — more reactive than season wOBA
- Same-game total ↔ K Over auto-tier elevation (when projected_total ≤7.5 → boost K Over conviction)
- Pitcher-vs-handedness lineup-specific OPS split
- Time-of-day cohort (day game vs night game NRFI/Over rates)
- In-division vs out-of-division cohort

## 🔒 Launch blockers (THIS WEEK)
- **RevenueCat integration** — paid tier subscription gating
- **Sentry integration** — error monitoring before App Store launch
- Pending app changes verification — bulk TestFlight build

## 📅 Post-launch v1.1 queue
- **Direction 2 Phase B** — wire pitcher class projections into `score_pitcher_er` + `score_pitcher_outs` PRIME-tier scoring (replace season xERA fallback when class sample n≥3)
- Surface lineup degradation flag in pipeline conviction scoring (not just scout)
- Surface ump audit-anchored hit rates on each pick card in app
- Add cohort-conditioned conviction badges ("PRIME 87 — boosted to 92 because UNDER environment, audit 80%")

## 🗓️ Longer-term parked
- Phase 2 (Nov+): NBA spread_delta cohort to audit_tier_calibration after 30+ regular-season games
- Wait 4 weeks (target ~5/14), re-run ML backtest with grown close-ML sample
- Re-evaluate bullpen_gassed cohort once n≥20 (~2-4 weeks)
- NCAAB Phase 2 activation (Nov — foundation already shipped 5/8)

## 📊 Audit checkpoints
- **~5/17:** re-run audit. Alt-line cohort should have 30+ K Overs with `_projected_ks`. Calibrate magnitude thresholds if a band underperforms.
- **~5/24:** two-factor combo cohorts ready to ship (most singles hit n≥80 by then)
- **~6/10:** Phase B class-projection integration ready (class data 30 days mature)

## 🎬 Strategic positioning recap (for content/copy)
Sweat Locker = depth + audit transparency vs Oddible's breadth + opaque AI grading. 
Key differentiators shipped today:
- "20% OVER hit rate when ump is under-friendly (n=15) — we'll auto-suppress those picks"
- "Long-rested home pitcher covers 57.1% ATS over 42 games — we elevate those"
- "Pitcher class projection: Buehler is 7.28 ERA-in-class over 7 starts vs 105 wRC+ offenses — fade tonight"
- "Two lineups degraded by 18.8 and 5.4 wRC+ pts vs season — POTD UNDER stacks"
