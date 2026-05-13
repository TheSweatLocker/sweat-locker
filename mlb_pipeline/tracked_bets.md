# Tracked Bets Log

Running log of parlays/bets we want to grade later. Newest first.
Grade the previous day's open entries at the start of each session.

---

## 2026-05-12 — Lotto Parlay ("for fun" build) — STATUS: GRADED — DEAD (1-7, one unknown)

8-leg lotto. Variance bombs flagged. Grade vs final box scores on 2026-05-13.

| # | Game | Leg | Tier/Conv | Result |
|---|------|-----|-----------|--------|
| 1 | LAA @ CLE | Slade Cecconi outs UNDER 16.5 | STRONG 76 | not graded (no prop row stored) |
| 2 | NYY @ BAL | Trevor Rogers ER OVER 2.5 | matchup-based | ✅ WIN (NYY 6-2) |
| 3 | COL @ PIT | Game total OVER 7.5 (POTD) | v2 edge +2.6 | ❌ LOSS (final 4) |
| 4 | KC @ CWS | Stephen Kolek outs OVER 17.5 | variance bomb | ❌ LOSS (4.2 IP) |
| 5 | ARI @ TEX | Game total UNDER 8.0 (Blaser ump fade) | audit cohort | ❌ LOSS (final 11) |
| 6 | SF @ LAD | Dodgers ML | confluence +7 + mastery agree | ❌ LOSS (SF won 6-2) |
| 7 | SF @ LAD | Adrian Houser ER OVER 2.5 | PRIME 88 (correlated w/ #6) | ❌ LOSS |
| 8 | PHI @ BOS | Carlos Narváez hits UNDER | PRIME 100 (gate-cleared) | ❌ LOSS |

Notes: legs 1, 4, 5 are the high-variance legs. 6+7 correlated. Not in the Daily Degen.

---

## 2026-05-12 — Best bets, deep-dive 4 games (ARI/TEX, SEA/HOU, STL/ATH, SF/LAD) — STATUS: GRADED 4-6-2

Standalone tracked plays from the deep re-report. Grade vs final box scores on 2026-05-13. Theme: fragile-starter signal cluster (L3 ERA + 1st-inn ERA + xERA vs opp lineup quality).

| # | Game | Leg | Tier/Conv | Thesis | Result |
|---|------|-----|-----------|--------|--------|
| 1 | ARI @ TEX | Zac Gallen outs UNDER 14.5 | STRONG 76 | L3 ERA 7.11, last out 4.0 IP, 1st-inn 6.8, xERA 5.45 — hooked by 5th | ✅ WIN |
| 2 | ARI @ TEX | Zac Gallen Ks UNDER 4.0 | STRONG 75 | proj 3.3 K; K% 14.8, 11.8pt below TEX contact rate (correlates w/ #1) | = PUSH |
| 3 | ARI @ TEX | Game total UNDER 8.0 | game-edge lean, ½u | cold lineups (2.9 L10 each) + Blaser slight-under ump; model disagrees (wants 10.2) | ❌ LOSS (11) |
| 4 | SEA @ HOU | Game total OVER 9.0 | game-edge, top of these 4 | model 11.16; Imai 11.57 1st-inn ERA, both pens gassed 9/9, Wolf 55% over | ✅ WIN (12) |
| 5 | SEA @ HOU | Bryan Woo Ks UNDER 4.5 | STRONG 81 | proj 4.1 K; K% 3pt below HOU lineup, 16% 1st-3rd K, short leash; pitches to contact (1.0 BB/9) | ❌ LOSS |
| 6 | SEA @ HOU | YRFI | bonus, correlated | NRFI 36, Imai 11.57 1st-inn, HOU 117 1st-inn wRC+ | ❌ LOSS (NRFI) |
| 7 | STL @ ATH | Andre Pallante Ks UNDER 4.0 | STRONG 81 | proj 4.1 K; K% 17.6, 8.2pt below ATH lineup, 13% 1st-3rd K, 1st-inn ERA 11.6 | = PUSH |
| 8 | STL @ ATH | Athletics ML / A's team total OVER | game-edge | confluence +3 home, ATH hot (+0.7 drift, 5.1 L10) vs STL fading, lineup OPS .76 vs .652, Pallante 1st-inn 11.6 | ❌ LOSS (STL won 6-4) |
| 9 | STL @ ATH | Thomas Saggese hits UNDER 0.5 | STRONG 78, bonus | 7 straight hitless, .000 L7 BA, #9 hitter ~3 PA vs Springs 3.50 xERA — pure variance | ✅ WIN |
| 10 | SF @ LAD | Adrian Houser ER OVER 2.5 | PRIME 88 | L3 ERA 7.36, 1st-inn 10.29, xERA 5.55 vs LAD 121 wRC+ — best pitcher prop on board | ❌ LOSS |
| 11 | SF @ LAD | Adrian Houser outs UNDER 14.5 | STRONG 80 | hooked early (correlates w/ #10); also a Houser Ks UNDER 4.0 STRONG 81 proj 2.7 available as 3rd correlated leg | ✅ WIN (correlated thesis split — short start but limited damage) |
| 12 | SF @ LAD | Dodgers run line -1.5 | game-edge | projected spread +2.72 (model expects LAD by ~3); RL > the -328 ML | ❌ LOSS (SF won outright) |

**Audit notes (for 5/17 review):**
- SF/LAD was the heaviest confluence game we've ever fired (+7) and it went entirely the wrong way. n=1, not a verdict, but flag for the `confluence_extreme_ge6` cohort (which is 5-2 STD per today's audit — this drops it to 5-3).
- The Houser "fragile-starter" cluster split: outs-under ✓ / ER-over ✗. Same signal, different expressions — short outing didn't compound into runs. Outs-under is the cleaner expression of the thesis; ER-over needs an additional gate (e.g., "AND opp lineup wRC+ ≥110 AND park ≥100").
- PRIME hits-OVER kept producing (Wood, House from Degen, Saggese under) — that tier remains calibrated. The hits-UNDER PRIME (Narváez, Chapman elsewhere) had a rough one — Narváez was the gate-cleared first live test and it lost. Monitor.

---

Skipped on purpose: ARI/TEX total *over* side and STL/ATH total — model-priced / coin-flip. Highest-signal subset: #4, #10, #11, #7, (+ #1 for a 5th).

---
