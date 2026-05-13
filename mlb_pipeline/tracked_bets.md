# Tracked Bets Log

Running log of parlays/bets we want to grade later. Newest first.
Grade the previous day's open entries at the start of each session.

---

## 2026-05-13 — ALL MODEL LEANS (every game, audit-grade) — STATUS: OPEN

Full per-game tracking of where the model sits on Total + ML/side + NRFI. Grade against tomorrow's box scores. This is the "where is the model right now" pulse — separate from the curated top-10 plays below.

| Game | Total lean | ML / side lean | NRFI | Conf | Notes | Result |
|---|---|---|---|---|---|---|
| STL @ ATH | no edge (-1.1) | Athletics slight (conf +2) | neutral (56) | +2 | | TBD |
| CHC @ ATL | no edge (+1.1) | no edge | neutral (46) | +0 | xERA gap fires OVER lean alt | TBD |
| NYY @ BAL | no market (model 6.48) | **NYY** (conf +4) | NRFI (83) | +4 | NYY -1.5 RL alt; Fried owns BAL | TBD |
| PHI @ BOS | no edge (-0.5) | **PHI** (conf +4) | YRFI (26) | +4 | Gray vs PHI career 17.18 ERA | TBD |
| KC @ CWS | no edge (-0.4) | CWS slight (conf +2) | neutral (53) | +2 | Wind in 9mph E | TBD |
| WSH @ CIN | borderline (+1.4) | **WSH** (conf +5) | YRFI (24) | +5 | **DAWG: WSH PRIME 89** · wind 24mph SE | TBD |
| LAA @ CLE | no market (model 6.6) | **CLE** (conf -5) | NRFI (100) | -5 | Pitcher duel — Messick vs Detmers | TBD |
| SEA @ HOU | **OVER 9.0** (+1.7) | no edge | YRFI (32) | -1 | HOU pen 5.61 ERA gassed | TBD |
| SF @ LAD | no edge (+0.9) | **LAD** (conf +4) | NRFI (100) | +4 | **POTD UNDER 7.5 (v2 -1.6)** — v3 disagrees w/ v2 here | TBD |
| SD @ MIL | **OVER 7.0** (+1.5) | **MIL** (conf +5) | NRFI (100) | +5 | Misiorowski PRIME K-over | TBD |
| MIA @ MIN | **OVER 9.0** (+2.5) | MIA slight (conf +3) | neutral (58) | +3 | Biggest total edge on slate | TBD |
| DET @ NYM | no edge (+0.1) | NYM slight (conf -3) | neutral (57) | -3 | Citi Field wind 37mph S — anomaly flag | TBD |
| COL @ PIT | no edge (-0.2) | **PIT** (conf +4) | YRFI (36) | +4 | Keller home edge | TBD |
| ARI @ TEX | no edge (-0.5) | no edge | YRFI (27) | +0 | Rocker/Nelson 1st-inn 11+/12+ | TBD |
| TB @ TOR | **OVER 7.5** (+1.7) | no edge | NRFI (100) | -1 | Cease PRIME K-over | TBD |

**Lean counts:**
- TOTAL leans fired: 4 OVERs (SEA/HOU, SD/MIL, MIA/MIN, TB/TOR), 0 UNDERs from v3 — but **v2 says UNDER 7.5 on SF/LAD (POTD)**. v3/v2 conflict on SF/LAD is the one to watch.
- ML / side leans (PRIME conf ≥4): NYY, PHI, WSH, CLE, LAD, MIL, PIT (7 of 15)
- NRFI / YRFI lean firing: 10 of 15 games at extremes (5 NRFI ≥70, 5 YRFI ≤40)

**Data oddities to flag:**
- NYY/BAL + LAA/CLE: `close_total` returned null — probably odds-API hiccup on these two, worth checking
- DET/NYM wind 37mph S at Citi Field — like yesterday's data anomaly, may be inflating any total signal

---

## 2026-05-13 — Best bets + sleepers — STATUS: OPEN

Grade vs 5/14 box scores. Top-10 anchored picks + the L7-rolling sleepers the standard scorer didn't surface (the model is under-surfacing ER-over and long-outs sides today).

| # | Game | Leg | Tier/Conv | Thesis | Result |
|---|------|-----|-----------|--------|--------|
| 1 | SF @ LAD | Total UNDER 8.0 (POTD) | POTD v2 -2.1 | model 5.9 vs 8.0; NRFI 100; Ohtani xERA 2.17 vs SF wRC+ 82 | TBD |
| 2 | SD @ MIL | Misiorowski Ks OVER 5.1 | PRIME 93 | proj 8.4; K gap +13.8 vs SD 25.7% K%; xERA 2.72 | TBD |
| 3 | WSH @ CIN | Nationals ML +134 | DAWG PRIME 89 | plus money on the side model rates straight-up favored; PRIME +5 WSH | TBD |
| 4 | TB @ TOR | Cease Ks OVER 5.1 | PRIME 93 | proj 7.7; K gap +13.6 vs TOR depleted lineup (Kirk/Santander/Barger out) | TBD |
| 5 | SF @ LAD | Ohtani Ks OVER 5.1 | PRIME 91 | proj 7.1; 0.00 ERA/.182 BAA career vs SF; xERA 2.17 | TBD |
| 6 | ARI @ TEX | YRFI | NRFI 27 | Rocker 1st-inn ERA 12.86, Nelson 11.05 — slate's loudest YRFI signal | TBD |
| 7 | WSH @ CIN | James Wood hits OVER 0.5 | PRIME 100 | park 108, wind 16mph SW out, Lodolo L3 6.75 ERA, Wood in 6/7 L7 | TBD |
| 8 | WSH @ CIN | Game total OVER 9.5 | v2 +2.2 | model 10.9; Lodolo torched; GABP park 108; wind out | TBD |
| 9 | NYY @ BAL | Max Fried outs OVER 17.5 | L7 sleeper | proj 19.4 (6.48 IP L7); owns BAL (2.95 ERA, .229); biggest L7-to-line outs cushion on board | TBD |
| 10 | PHI @ BOS | YRFI | NRFI 26 | Sonny Gray career vs PHI 17.18 ERA/.421 BAA; Painter 1st-inn 6.0; double-fragile | TBD |

**L7-rolling sleepers (not in PRIME prop list but data is loud):**
| # | Game | Leg | Type | Thesis | Result |
|---|------|-----|------|--------|--------|
| S1 | MIA @ MIN | Simeon Woods Richardson ER OVER 2.5 | sleeper | L7 4.0 ER, K gap -14.1 vs MIA contact, 1st-inn fragile — model under-surfaced | TBD |
| S2 | SEA @ HOU | Lance McCullers ER OVER 2.5 | sleeper | L7 4.0 ER, career vs SEA 9.53 ERA/.312 BAA, 1st-inn ERA 6.43 | TBD |
| S3 | PHI @ BOS | Sonny Gray ER OVER 2.5 | matchup-history | L7 only 1.86 but career vs PHI 17.18/.421 — matchup override; half-unit | TBD |
| S4 | KC @ CWS | Seth Lugo outs OVER 14.5 | sleeper | L7 5.9 IP = ~17.7 outs; UNDER-environment game means he goes deep | TBD |
| S5 | CHC @ ATL | Imanaga BB UNDER 2.5 + HA UNDER 5.5 stack | sleeper | proj 1.57 BB / 3.57 H; elite command but road-split concern (1.74H/3.31A) — half-unit | TBD |

**Correlated alternates (paired with main legs):**
- Ohtani ER UNDER 1.5 (PRIME 87) + Ohtani HA UNDER 5.5 (PRIME 81) — same start as #1+#5
- Misiorowski HA UNDER 5.5 (PRIME 81) — same start as #2
- Brady House / Daylen Lile hits OVER (PRIME 100 each) — same game as #7+#8 (WSH stack)
- Painter Ks UNDER 4.5 (STRONG 81) + Gray Ks UNDER 4.0 (STRONG 78) — same game as #10
- Rocker Ks UNDER 4.0 (STRONG 81) — same game as #6
- Matt Chapman hits UNDER 0.5 (PRIME 100) + Bader UNDER (PRIME 96) — SF fade stack, same game as #1
- Laureano/Machado/France/Castellanos/Sheets/Andujar hits UNDER (PRIME/STRONG 81-100) — SD fade stack, same game as #2

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
