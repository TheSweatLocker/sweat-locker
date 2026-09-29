---
name: Pitcher Offense-Class Projections (Direction 2 Phase A — 2026-05-10)
description: Per-starter projection of expected IP/ER/Outs based on actual gameLog performance vs offenses bucketed by current wRC+. Surfaces in scout_report. Phase B (wire into ER/Outs prop scoring) queued for post-launch v1.1.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Shipped Phase A of Direction 2 on 2026-05-10. For each starter, fetches MLB Stats API gameLog (current + prior season) and aggregates per-game performance bucketed by opponent's CURRENT wRC+ band.

**Class buckets (asymmetric):**
- `le_90`, `91_100`, `101_110`, `111_120`, `ge_121`

**Output per starter per bucket (when n≥2):** avg IP, ER, Outs, K, BB, HR + ERA-in-class

**File locations:**
- Builder: `mlb_pipeline/compute_pitcher_class_projections.py`
- Cache: `mlb_pipeline/data/pitcher_class_projections.json` (daily refresh)
- Scout integration: `mlb_pipeline/scout_report.py` — new `── PITCHER CLASS PROJECTIONS ──` section per game, falls back to nearest class with data when target bucket empty
- Cron: `.github/workflows/mlb_pipeline.yml` — runs nightly between MLB run and NBA picks

**Pragmatic shortcut:** uses CURRENT season wRC+ as proxy for "opponent quality at time of historical game" instead of historical wRC+ snapshot. Cleaner backfill is hard (lineups shift) but the proxy works for most matchups since team-level wRC+ doesn't drift wildly mid-season.

**Calibration note:** wRC+ proxy uses `OPS / 0.720 * 100` since no persistent team_stats table. Most teams land in 91_100 or 101_110 buckets — elite teams (Cubs/Dodgers at 121-123 actual) get classified in 111_120 by the OPS proxy. Acceptable for Phase A; replace with FanGraphs API or compute_from_woba in Phase B if accuracy issues surface.

**Day-1 findings worth flagging:**
- **Walker Buehler vs ~105 wRC+ class: 4.24 IP / 3.43 ER, 7.28 ERA-in-class on 7 starts** — terrible in this class. Tonight (5/10) facing Cardinals (105 wRC+) at home; class projection says Buehler vulnerable despite his name recognition.
- **Cade Cavalli vs ~104 wRC+: 3.61 IP / 3.0 ER, 7.48 ERA-in-class on 6 starts** — Cavalli projects to get rocked by Nationals tonight.
- **Sandy Alcantara vs ~106 wRC+: 5.48 IP / 3.56 ER, 5.84 ERA-in-class on 9 starts** — historically below season expectation against this class. Fade signal.
- **deGrom: no n≥2 sample at Cubs' 120 wRC+ band**, falls back to 101_110 showing 6.1 IP / 1.69 ER over 16 starts. He's elite vs 101-110 class — Cubs hot but historically he handles this tier.
- **Brenan Hanifee opener-tagged: 1.2 IP / 0.1 ER vs 101_110 class** — historically used as opener even vs above-avg lineups, confirming opener flag.

**Phase B (queued, post-launch v1.1):** wire class projections into `score_pitcher_er` and `score_pitcher_outs` in generate_props.py — replace season xERA fallback with class-matched projection when sample n≥3. Add audit cohort `er_projection_class_*` to track projection accuracy vs actuals.

**Known limitations:**
- Sample size sparse for ge_121 bucket (Cubs/Dodgers tier) — many starters have n<2 and fall back to 101_110 nearest
- OPS-derived wRC+ proxy underestimates elite teams; refine in Phase B
- New rookies (e.g., Spencer Miles 12 starts) get tight buckets that may not generalize

**Strategic value:** This is the depth differentiator vs Oddible. Their "AI grades bets Great/Good/Fair/Bad" can't say "this pitcher historically allowed 3.43 ER per start vs offenses similar to tonight's opponent over 7 starts." Audit-anchored matchup-quality projection is publishable content + bet-quality input.
