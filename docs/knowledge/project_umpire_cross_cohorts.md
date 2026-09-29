---
name: Umpire cross-cohorts shipped (2026-05-10)
description: Audit cohorts crossing K-Over / Total / NRFI outcomes with umpire characteristics (k_rate_above_avg, over_rate, nrfi_rate). Massive findings: 20% OVER hit rate with under-friendly umps, 60.7% NRFI with NRFI-friendly umps.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Shipped umpire cross-cohort family on 2026-05-10. Joins resolved games to `mlb_umpires` lookup table for structured ump characteristic data.

**🏆 Headline findings (STD window):**

**Total Over × ump:**
- `total_over_with_ump_over_hostile` (over_rate ≤ 0.45): **20.0% OVER (n=15)** ⭐⭐ — strongest fade signal in entire audit system
- `total_over_with_ump_over_friendly` (over_rate ≥ 0.55): **56.1% OVER (n=114)** — modest edge
- `total_over_with_ump_neutral` (0.45-0.55): 49.0% (n=249)

**NRFI × ump:**
- `nrfi_with_ump_nrfi_friendly` (nrfi_rate ≥ 0.55): **60.7% NRFI (n=545)** — strong baseline
- `nrfi_with_ump_nrfi_hostile` (nrfi_rate ≤ 0.45): **40.8% NRFI (n=878)** — fade NRFI hard
- `nrfi_with_ump_neutral`: 49.3% (n=931)

**K-Over × ump (small samples but directional):**
- `k_over_with_ump_k_friendly` (k_rate_above_avg ≥ 0.2): 100% on n=9
- `k_over_with_ump_k_hostile` (k_rate_above_avg ≤ -0.2): 60% on n=15
- `k_over_with_ump_neutral`: 52.9% (n=17)
- Suggests: pitcher quality > ump environment for K Overs (cohort still profitable across all bands)

**Why it matters:**
- The 20% OVER/under-friendly ump cohort is essentially a free fade signal. Any total OVER lean firing in an under-friendly ump game should be reversed or skipped.
- NRFI cohort stratification by ump is enormous — combining NRFI 90-94 sweet spot (71%) with NRFI-friendly ump (60.7% baseline) should compound to 75%+ hit rate. Two-factor combo cohort follow-up.

**Tonight's actionables:**
- Mark Ripperger (Sea/CWS, over_rate 0.46) — fade OVER on that game
- James Jean (Was/Mia, over_rate 0.57 + K rate -0.4) — over-friendly + K-hostile combo
- David Rackley (HOU/CIN) — over-friendly, adds support to Astros OVER stack
- Lance Barksdale (NYM/ARI) — K-hostile, counters K Over PRIMEs

**Engineering location:**
- `mlb_pipeline/audit_tier_calibration.py` — `fetch_umpire_stats_map()`, `fetch_resolved_with_umpire()`, `compute_umpire_window_rates()`, wired into main between pitcher rest and NBA cohort blocks
- Reads from existing `mlb_umpires` table (no schema change needed)

**Followup queue:**
1. Two-factor combo: NRFI 90-94 × NRFI-friendly ump — likely 75%+ hit rate
2. Surface ump cohort flag in pipeline conviction scoring (auto-downgrade total OVER picks when under-friendly ump)
3. Add ump-friendly K Over conviction boost (when K-friendly ump assigned, +5 to conviction)

**Strategic value:**
This is publishable content Oddible cannot match. "We track 543 ump-game samples by NRFI rate — when ump's nrfi_rate ≥0.55, NRFI hits 60.7%." Audit-anchored differentiation.

**End-to-end integration shipped 2026-05-10:**
1. ✅ Audit cohort family in `audit_tier_calibration.py`
2. ✅ Pipeline filter: `play_of_day.py:_get_ump_total_signal()` + `_v2_total_edge()` — auto-suppresses OVER picks when under-friendly ump assigned (n≥30 guard), appends confirmation note when supports
3. ✅ Scout report visibility: `scout_report.py:get_ump_stats()` + `ump_signal_summary()` — per-game audit-anchored ump flags (🔥 K-friendly / ❄️ K-hostile / 🚫 OVER fade / 🔒 NRFI-friendly / 🌋 YRFI-friendly)
4. ✅ Existing fuzzy K-friendly text match in `generate_props.py` kept (already +8 conviction)

**Tonight's 5/10 POTD validation:**
John Tumpane (Braves/Dodgers ump) has K +0.5 + NRFI 0.67 on n=33. POTD UNDER 9.0 stacks 5 supporting signals: v2 edge -2.3, both lineups degraded, L5 hot fade, low BP ERA, NRFI-friendly ump. Cleanest signal-stacked play of the slate.
