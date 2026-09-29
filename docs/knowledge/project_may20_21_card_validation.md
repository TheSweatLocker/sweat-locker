---
name: project-may20-21-card-validation
description: "Public card went 20-4 over 5/20-5/21 (84.6% per day). Pre-launch validation moment — the architecture work shipped during this run, no contradiction."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Two-day public card run: 20-4 (83.3%)** — pre-launch validation that the system is working as intended.

**5/20 — 11-2 (84.6%)**
- POTD: MIL/CHC NRFI 90 ✓
- DotD: Brewers ML +100 ✓
- Daily Degen 4-leg: 4/4 (NRFI + Sale Ks Over + Ohtani ER Under + Freeland ER Over)
- PRIME props: 18-6 (75%)
- STRONG props: 8-3 (72.7%)

**5/21 — 9-2 (81.8%)**
- POTD: CLE/DET NRFI 90 ✓
- Big v4 edge call: PIT/STL Over 7.0 (v4 said 9.21, actual 8) ✓
- ATL ML PRIME (caught by today's compute_primary_play v4-aware fix — was Atlanta -3.83 v4 conviction)
- User made money personally using the card

**Why this matters:**
- Validates the architecture cleanup (server-driven sweat card, v4-aware compute_primary_play, audit-driven POTD selector) is producing — not breaking — picks
- Validates the trap zone audit (raised STRONG ML threshold from 1.5 → 2.0): high-edge picks at ≥2.0 are the real signal, low-edge picks were the noise we just suppressed
- Validates the cohort calibration system: cohorts that scored above 65% L30d (nrfi_prime_90_94, confluence_extreme_ge6, k_under_strong) were the ones that paid out
- Validates the prop-signal override layer: on 5/20 LAD/SD, v4 said OVER but prop signal said UNDER -8 — we recommended Ohtani PRIME props instead of LAD ML, and props cashed while LAD lost

**Architecture wins shipped 5/21 (same day as 9-2 card):**
- Sweat card `top_props` unified surface (server-driven)
- Sweat tier reads from server (no client cutoff calc)
- compute_primary_play v4-aware (caught ATH ML 5/20, ATL ML 5/21)
- Dawg of Day v4-aware edge gate
- KenPom user-facing references stripped (5 spots)
- 170 lines of dead MLB code ripped from calcGameSweatScore
- Jerry name accuracy rule added to universal template
- Spread_delta trap zone fix (1.5 → 2.0) + wRC+ cohort surface
- NCAAB skeleton (SQL + offseason placeholder + pipeline + backtest harness)
- v5 retrain attempted, no improvement → v4 stays live (memory: project_v5_retrain_no_change)

**Pattern lesson:** The deeper architecture and the user-facing wins move together. Both days the system was both producing picks AND being hardened — no false trade-off between "ship features" and "fix architecture."

**Operational state heading into 5/22 (day off, big launch-ops day):**
- MLB pipeline: 🟢 fully server-driven, audited, calibrated
- NCAAB foundation: 🟢 built, November activation = 1 day of work
- NBA/NFL/NHL: 🟡 client-side fallback for v1.0, proper pipelines = v1.x
- Remaining launch blockers: RevenueCat + paywall, App Store Connect, TestFlight, submit
- Target: end-of-May App Store submission (~9 days)

**Next session focus:** All launch ops, no more model work. v4 + the trap-zone fix carry production through review window. The card is hot — don't tinker with what's working.
