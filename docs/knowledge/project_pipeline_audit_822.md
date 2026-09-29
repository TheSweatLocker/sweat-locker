---
name: project-pipeline-audit-822
description: 8/22 full pipeline cadence audit + Batch 1-3d shipments — 30 min/cron saved via tier-gate + season gates + cross-sport cleanup.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-22T18:11:07.049Z
---

**8/22 pipeline efficiency sprint.** 4 parallel investigation agents mapped every workflow (15 files), every script (~330), and all data flows. Consolidated audit published as artifact. Shipped Batch 1-3d in ~9 focused commits, no data loss.

**Why:** MLB pipeline was hitting 60+ min per cron. User priority: efficiency + not-breaking-things + cross-sport parity + reducing paid-API waste (esp. KenPom).

**How to apply:** future changes to sport scripts should go through `season_gate.py` for in-season checks and `--tier-gate` for LLM cost control. Never `rm` legacy — move to `_legacy/` subdirectory (safer, preserves in git).

**Shipped commits:**
- `dc30f301` StrongestSignalCard matchup parse (frontend)
- `c68b2b61` matchup 'game' placeholder root cause (backend + frontend safety net)
- `2e6a9b76` StrongestSignalCard uses picks prop (was silently ignored — 5th user report on this)
- `2ba623cf` **Batch 1:** --tier-gate on prop Jerry synth + season_gate.py helper + NFL preseason removal + cross-sport --force cleanup + KenPom Monday dedup + UFC reads moved to ufc_pipeline.yml
- `6a16dead` **Batch 2a:** render_prop_template.py deterministic renderer for below-gate props
- `3739f65d` **Batch 2c:** cold-streak guard on prop scorer (L10≤2 AND season<25% → PASS)
- `8cf25efc` **Batch 2d:** prop write dedup on (game_date, player, type, dir, line)
- `ee4b69e2` **Batch 3d:** season_gate applied to 8 cross-sport scripts (NCAAF/NCAAB/NBA/NHL)
- `385b02a7` **P2:** sklearn InconsistentVersionWarning suppression

**Deferred to focused sessions (needs care, not urgent):**
- Batch 2b: NFL prop table unification (nfl_props vs nfl_pipeline_props) — off-season, unblock before Sept
- Batch 3a: consolidate `generate_mlb_game_reads.py` + `generate_jerry_synthesis.py` into one script
- Batch 3b: consolidate 4 tier calibration layers (prop_ensemble_scorer + apply_prop_refit + prop_tier_calibration + Jerry caps)
- Batch 3c: split mlb_pipeline.yml (135 steps 2×/day) into daily_am / daily_pm / intraday / grading workflows

**Estimated savings per MLB cron: ~30 min**
- Prop Jerry LLM waste (~21 min): 332 Claude calls → 61 (PRIME+STRONG only) + template for the rest
- Cross-sport calls off MLB cron: ~3-5 min
- NFL preseason skip: ~1-2 min in-season, all of it off-season
- KenPom paid API: halved (Monday duplicate removed)

**Key architectural finding:** pipeline grew by accretion. Every new capability shipped as new script alongside old, never replacing. Result: 20+ duplication clusters, multiple layer-cake calibration passes, monolithic MLB workflow calling other sports' scripts. Audit surfaces the full map for methodical cleanup.

**Related:** [[project_all_sports_readiness_820]], [[project_playbook_shadow_tracking_820]], [[feedback_backside_dictates_app_renders]].
