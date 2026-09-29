---
name: Under props calibration tracking
description: Initial K Under and Hits Under prop results to inform tier threshold tuning, shipped 2026-04-30 in commit 0c4128b
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
K Under + Hits Under scorers shipped 2026-04-30 (commit 0c4128b).

**Why:** 94-game audit showed Over-only props were leaving the contact-lineup-fades-aspirational-K-line angle on the table. Under thresholds set higher than Overs (K Under STRONG 70 / PRIME 82, Hits Under STRONG 75 / PRIME 85) because the outcome is rarer.

**How to apply:** Track Under hits/misses informally until the May 14 audit fires (one-shot job 43f55334). If McCullers-style misses (where signals say "fade" but pitcher rebounds with 9 K) cluster in a specific signal pattern, tighten that signal. Don't retune thresholds off n<10 — wait for audit.

**Day 1 results (2026-04-30):**

Ks Under (stored, 3):
- Houser ks_under 4.0 (PRIME 87): 2 K — HIT ✓
- Young ks_under 4.0 (STRONG 75): 2 K — HIT ✓
- McCullers ks_under 4.5 (PRIME 82): 9 K — MISS ✗

Hits Under (stored, 0 — floor HITS_UNDER_CUTOFF=70 filtered all candidates):
- Greg Jones near-miss at conviction 69: 0-fer (would have HIT at lower floor) — 1-of-7, .053 L7 BA, 4-game hitless streak, hitting 8th vs 26.5% K opp
- Suggests HITS_UNDER_CUTOFF may be one tier too tight; consider 65 if pattern holds. Wait for audit before retuning.

McCullers signals that fired: negative k_gap, fading L3 K%, bad framing, slow 1st inn, short leash. The "short leash" signal failed (he went deep). Possible miss pattern: when L3 ERA is bad but season K% is still ≥22%, the "fading" signal can be noise — pitcher's stuff still plays. Worth testing whether requiring season K% ≤22 (not just L3 fade) tightens PRIME further.

---
**UPDATE 2026-05-11 — Hits-UNDER record + PRIME multi-signal gate shipped:**

Resolved hits_under record (n=102): **62-40 overall (60.8%)**. By tier: PRIME (conviction ≥85) = **26-21 (55.3%)** vs STRONG (75-84) = **29-14 (67.4%)**. PRIME *underperformed* STRONG by 12 pts — the score stacks team-level + noise signals (weak offense, pitcher park, K-friendly ump) to reach PRIME without an individual reason THAT batter goes 0-fer.

**Fix shipped (generate_props.py:score_batter_hits_under):** PRIME multi-signal gate. Conviction ≥85 now requires BOTH:
1. Genuinely elite opp pitcher (xERA ≤ 3.0)
2. An individual factor: bats bottom of order (lineup_position ≥ 7), OR personally ice-cold L7 (got_hit_rate ≤ 0.30), OR active hitless streak ≥ 3 games
Else → cap at 84 (STRONG, the better-performing tier). Mirrors the K-Under PRIME gate pattern.

5/11 impact: zero — all 3 SF/LAD hits-UNDER PRIMEs (Freeland 92, Rushing 88, Kim 85 vs McDonald 2.81 xERA) cleared the gate cleanly. Gate is targeted, not blunt.

K-Under tier record as of 5/11 (n=27): PRIME 5-4 (55.6%), STRONG 11-5 (68.8%). Same pattern — STRONG outperforms PRIME. K-Under PRIME gate already in place since 5/7.

**Pattern worth noting:** for BOTH UNDER prop types, STRONG > PRIME. The conviction scoring over-rewards signal stacking. Watch whether the gates fix this — recheck at 5/17 audit.
