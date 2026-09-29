---
name: project-predictive-signals-backlog-907
description: "🎯 9/7: NFL model-improvement backlog — 5 immediate-win signals + 3 medium + 3 big, plus Wk1-4 things we won't know until games play"
metadata:
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T21:29:43.627Z
---

**Queued 9/7 evening** — comprehensive audit of what could make NFL models more predictive. Data-in-hand vs data-we-need-to-fetch vs stuff-we-won't-know-until-Week-1.

## ✅ Shipped 9/7 (Immediate wins from data-in-hand)

Ported to `nfl_generate_props.py::_emit_nfl_ctx_signals` — additive signals with conviction bonus/penalty. All low-risk (additive, don't touch core ensemble math).

1. **div_game weight** — Div rivals hit UNDER 53% + spreads tighter. Nudges skill props toward UNDER on div games.
2. **Bye-week rest advantage (≥13d)** — Bye teams cover ATS ~55%. +4 bonus on skill OVERs.
3. **CPOE gap** — Home vs Away pass_cpoe delta ≥5pt → +4 pass_yds edge to higher side; +3 INT-under boost for accurate QBs.
4. **H2H last-5 total vs market** — When h2h_last5_avg_total is 5+pts off close_total → historical trend signal (+3 in matching direction).

Previously shipped in the same function: weather (wind + temp), short week (rest ≤4), game script (implied total + spread magnitude), QB vs opponent history, injury outs.

## 📋 Medium wins queued (1-2 days each, needs some new plumbing)

5. **Weather → ensemble total (game-level, not prop-level)**
   - Currently `_emit_nfl_ctx_signals` applies to props. Ensemble scorer for GAME totals doesn't consume weather.
   - Fix: add a total adjustment factor in `ensemble_scorer.py::_score_market` when market='total' + ctx has outdoor + wind ≥15 or temp ≤32.
   - Impact: sharper total tier assignment on weather-affected games.

6. **Rest differential → spread edge (game-level)**
   - Bye vs short-week matchup should shift projected spread. Currently `_emit_nfl_ctx_signals` handles this per-prop but not per-game.
   - Fix: add rest_delta bonus in ensemble spread pick when |home_rest - away_rest| ≥ 4.

7. **Sharp$ + line-movement confluence**
   - We track `line_movement_flags` + `cleatz_signals` + `fadereport_signals` + `public_splits_v2` separately.
   - When 2+ sources agree on sharp side AND line moved in that direction → very strong ensemble signal.
   - Fix: aggregate confluence in `_score_market` as a +tier bump when combined.

## 🔍 Bigger swings (1 week+ each)

8. **Injury reactivity feed** — inactives publish 90 min pre-game. No current re-compute trigger for panel + tier post-inactive. Requires cron every 30 min from 90min-pre-game to game-time to re-run `nfl_panel_projection.py`.

9. **Referee crew total tendencies** — public data. Some crews call more penalties → longer games → more scoring. New table `nfl_referee_tendencies`.

10. **PFF grades** — paid ($$) but weighted-value stats are legitimate edge. Post-launch after we can justify the licensing cost.

## 🔎 Data NOT in stack (would require sourcing)

- PFF grades ($$$)
- Vegas Insider power ratings (competitor signal)
- SIS Data injury-impact scores ($$$)
- NextGen Stats time-to-throw / separation (public but needs scrape)

## 🔮 Won't know until Wk 1+ plays out

**Tier calibration accuracy** — 80% NFL props labeled STRONG. If they hit 55%+ → real. If 40% → recalibrate. Wk 3 has enough sample.

**Panel vs actual** — Wk 1 spot-check. JAX@CLE panel total 40.59 vs market 40.5. If actual scores ~41 → honest; if systematic under-projection → bias correction needed.

**LR overlay hit rate** — LR endorses 10/16 today. If LR-endorsed picks beat pipeline-only by ≥5pp → keep. If flat or worse → drop to shadow.

**Prop chart predictive power** — "8-of-10 OVER" as a signal. If chart winners hit 70%+ → publishable edge. If 55% → narrative only.

**Sharp$ source accuracy per market** — 4 sources (cleatz/fadereport/OC/SO). Which is most predictive on totals vs sides vs props? Bucket Wk 1 outcomes.

**Injury reactivity cost** — how many wrong tier assignments does the current no-live-update path cause? Wk 1 tells us if the reactivity feed is priority.

**v3 vs v4 disagreement** — v4 comes online Wk 3-4. When v3 & v4 disagree by 2+ pts, what wins? Need a resolver before it's a real problem.

**Ladder failure rate on weekly cadence** — MLB Ladder is 42% overall hit; NFL cadence is 1/week vs daily. Drawdown feels different. Wk 1-4 tells us if tier gate is too loose.

**NFL prop-vs-game hit split** — MLB: props 55%, sides 50%. NFL split unknown. Might be MORE predictable in NFL (usage more stable than MLB starter variance).

## Post-launch shipping order

**Wk 2 ship (from data-in-hand):**
1. Weather → total adjustment in ensemble scorer
2. Rest differential → spread edge in ensemble
3. Sharp$ + line-move confluence (medium tier bump)

**Wk 3-4 ship (needs 2 wks of resolved games):**
1. Tier recalibration based on actual Wk 1-2 hit rates
2. Panel bias correction if systematic
3. LR keep/drop decision

**Wk 4+ ship (needs full month):**
1. CPOE ensemble weight (already in props today — ship for games once calibrated)
2. Injury reactivity cron
3. Sharp$-source weight per-market bucketing

## Related memories
- [[project_nfl_model_pipeline_discussion_907]] — the deep-dive request
- [[project_technical_reference_manual_906]] — the reference doc that should include the pipeline diagram from 9/7 audit
- [[docs/NFL_MODEL_PIPELINE_AUDIT.md]] — file-based full walkthrough
