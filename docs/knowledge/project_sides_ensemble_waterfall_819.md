---
name: project-sides-ensemble-waterfall-819
description: "CORRECTED 8/19 — sides ensemble_v2 EXISTS (ensemble_scorer.py, signal-registry pattern, cutover 8/17). Legacy waterfall is fallback only."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-19T14:33:00.413Z
---

**CORRECTION to earlier draft:** The MLB sides ensemble scorer DOES exist as a true weighted evidence-aggregation engine — [ensemble_scorer.py](mlb_pipeline/ensemble_scorer.py), signal-registry / plug-in architecture, iterates every signal_sources row for the sport and aggregates weighted opinions per market.

**Cutover timeline:**
- Pre-8/17: legacy waterfall (game_context.py compute_primary_play) wrote primary_play
- 8/17: cutover began — recompute_primary_play.py tries ensemble_v2 first, falls back to legacy only if ensemble returns None
- 8/19 (today): all 15 games running ensemble_v2 ✅

**Prior audit (45.5% PRIME ML n=22 over 14d, 95% MC-HC dominance) measured the LEGACY WATERFALL — not the new ensemble.** Ensemble v2 has only ~3 days of live data (8/17-8/19, small sample). Cannot conclude ensemble is bad from pre-8/17 data.

**Real open issues:**
- 8/18 half-slate fell back to legacy (7/15 games) — ensemble returned None for some — need to fix so ensemble always emits
- Ensemble_v2 3-day hit rate is on a tiny sample (STRONG 1-2, LEAN 6-7) — need more data before drawing conclusions
- Signal_sources registry needs audit: what signals are configured, what weights, are ATS trends actually included?
