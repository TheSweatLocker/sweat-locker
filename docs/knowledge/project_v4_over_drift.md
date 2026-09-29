---
name: project-v4-over-drift
description: "v4 XGBoost totals model has calibration drift toward over-projecting runs in May. 30d hit 43% on OVER picks, 7d hit 41%, 3d 25%. UNDER picks still healthy (55% 30d). Suppressed OVER surfacing 5/24; v5 retrain queued."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Problem (2026-05-24 audit_v4_totals.py)**:
v4 totals model is genuinely broken on the OVER side. Numbers don't lie:

| Window | v4 OVER picks | v4 UNDER picks |
|---|---|---|
| 30d | 43.2% (n=111) | 55.1% (n=49) |
| 14d | 46.3% (n=82) | 55.9% (n=34) |
| 7d  | 40.9% (n=44) | 37.5% (n=8) |
| 3d  | 25.0% (n=20) | 50.0% (n=2) |

Trending DOWN on OVERs. v4 picks OVER ~3x as often as UNDER, so the OVER
miss rate is the dominant story.

v3 (formula model) outperforms v4 across all windows on totals:
- 30d: v3 51.1% vs v4 46.9%
- 7d:  v3 53.6% vs v4 40.4%

Worse: large v4-vs-market edges audit WORSE than small edges. 30d
|v4-line|≥1.5 hits 45.6%, |edge|<1.5 hits ~50%. When v4 says line is way
off, v4 is wrong more often. Counter-intuitive — usually high-confidence
picks audit best, but the model has lost calibration enough that its
confident calls are biased noise.

**Root cause hypothesis**:
v4 was trained on April data when team offenses were hotter. League-wide
L14 OPS in May shows most teams cooling (offense_drift signal shows most
teams below season baseline). Model keeps projecting season-baseline run
scoring; the actual May run environment has shifted down.

This is exactly what the L14 OPS work (project_v11_recency_wrc) is
supposed to fix at the INPUT layer — but the v4 model itself was trained
without that signal, so it can't adjust.

**Shipped today (2026-05-24) — directional asymmetric throttle**:
1. `compute_primary_play` in game_context.py: v4 OVER picks suppressed
   from primary play surface. UNDER picks still fire normally (55% 30d).
2. `find_total_edges` in generate_sweat_card.py: same — OVER edges
   filtered out of sweat card top_8 surfacing.
3. Both controlled by `V4_OVER_SUPPRESSED = True` flag. Flip back to
   False when 7d v4 OVER hit rate climbs back above 50%.

Did NOT do (intentionally):
- Full v4 kill — UNDER side is healthy, no reason to throw it out
- Revert to v3 for totals — would lose v4's better feature handling
  on cases where it IS right
- v5 retrain — queued for post-launch, needs stable June data first

**Queued v1.1 work**:
1. Recency-weighted v5 retrain (weight last 30d of training samples 3x)
2. Track v4 OVER hit rate in `model_health` table; auto-flip the
   suppression flag when 7d climbs back above 50%
3. Consider building separate v4-spread + v4-totals models if v4-totals
   stays bad even after retrain — they're different prediction targets

Related: [[project_may17_xgboost_degradation]] (the same model showed
warning signs back then — degradation has continued, not recovered).
