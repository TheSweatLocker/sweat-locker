---
name: project-prop-playbook-design-817
description: "2026-08-17 design doc for porting props to plug-in playbook. Schema, scorer, shadow-mode, first signals."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-17T18:12:52.046Z
---

Extension of [[project-prop-playbook-port-817]] — the technical design
before coding starts.

## Schema decision: extend signal_sources with subject_scope

Extend existing `signal_sources` (not build a new prop_signal_sources
table) with `subject_scope TEXT DEFAULT 'game'`. Values:
- `'game'` — evaluated against `ctx` (game_context row). All 148 today.
- `'prop'` — evaluated against `(ctx, p)` where p is the prop row.
- `'player_prop'` — evaluated against `(ctx, p, player)` where player
  is a bundle of the player's recent stats (L5, L10, season splits).

Prop signals use `market_scope` for prop_type restriction:
- `market_scope='hits'` fires only on hits_over/hits_under props
- `market_scope='*'` fires on any prop type
- `market_scope='pitcher'` fires on pitcher props (bb/ha/ks/outs/er)

## Prop ensemble scorer

`prop_ensemble_scorer.py`, analogous to `ensemble_scorer.py`:
- Iterate over signal_sources rows where subject_scope IN ('prop', 'player_prop')
- For each prop row, evaluate matching signals
- Aggregate contributions per direction (BACK/FADE)
- Assign tier from same TIER_THRESHOLDS
- Store shadow output in `mlb_pipeline_props.playbook_conviction` (new col)
  and `mlb_pipeline_props.playbook_sources` (JSONB — audit trail)

## Shadow-mode alongside legacy

Legacy `conviction` + `refit_conviction` + `tier` stay authoritative.
Playbook writes to NEW columns (playbook_conviction / playbook_tier /
playbook_sources) without touching existing. Nightly comparison job
tracks hit-rate: legacy tier vs playbook tier.

Cutover criteria (like game ensemble on 8/16): 14 days of shadow data
showing playbook ≥ legacy ROI on same picks.

## Player context bundle

`player_prop` signals need L5/L10/season data per player. Options:

**A. Fetch on the fly per prop** — signal handler queries player_stats
   table per evaluation. Simple, slow (30-50 signals × 30-50 props =
   thousands of queries per run).

**B. Pre-compute per-player context row** — new `prop_player_context`
   table populated nightly with all L5/L10/season splits any signal
   might need. Fast, requires knowing all fields upfront.

**C. Extend mlb_pipeline_props with per-prop L5/L10 hit rate columns** —
   populate at prop-generation time by generate_props.py. Simple, no
   extra table, keeps data with the prop row.

**Recommendation: C**. Least infra, keeps prop row self-contained,
generate_props already computes some of this (buried in `_apply_l5_signal`).

Fields to add to mlb_pipeline_props (also to nfl/ncaaf variants):
- `player_l5_hit_count` INT  (out of 5, on the same prop line)
- `player_l10_hit_count` INT (out of 10, on the same prop line) — NEW per user 8/17
- `player_season_hit_pct` NUMERIC (season rate at this line)
- `player_l5_extreme_flag` BOOL (>=4 or <=1 out of 5)
- `player_l10_extreme_flag` BOOL (>=8 or <=2 out of 10) — NEW

## First 3 signals to seed (proof-of-concept)

1. **player_l10_vs_line_extreme** (per user 8/17)
   - subject_scope='player_prop', market_scope='*'
   - condition: `l10_hit_count >= 8 or l10_hit_count <= 2`
   - side: BACK if extreme matches direction, FADE if opposite
   - strength: `abs(l10_hit_count - 5) / 5.0`

2. **refit_conviction_strong** (surfaces existing refit as a plug-in signal)
   - subject_scope='prop', market_scope='*'
   - condition: `p['refit_conviction'] is not None and p['refit_conviction'] >= 70`
   - side: BACK (direction preserved from prop)
   - strength: `(p['refit_conviction'] - 50) / 50.0`

3. **hits_over_juice_trap** (surfaces the batter_hits_juice_trap_803 pattern)
   - subject_scope='prop', market_scope='hits'
   - condition: `p['prop_type']=='hits_over' and p['direction']=='over' and p['tier']=='PRIME' and p['book_line'] and p['book_line'] <= -180`
   - side: FADE
   - strength: 0.6

## Migration sequence

1. Migration A: add `subject_scope` column to signal_sources (default 'game')
2. Migration B: add per-prop lookback columns to mlb_pipeline_props
   (+ nfl/ncaaf variants when their prop pipelines ship)
3. New file: `prop_ensemble_scorer.py`
4. Extension to `generate_props.py`: populate the new lookback columns
   at prop-creation time (uses existing player stat fetchers)
5. Wire into pipeline: run prop_ensemble_scorer AFTER apply_prop_refit,
   BEFORE generate_prop_jerry_synthesis
6. New audit: `audit_prop_playbook_shadow.py` compares legacy tier vs
   playbook tier daily, tracks hit-rate divergence

## Open questions before coding

- **Q1**: Do we ALSO port NFL props (nfl_pipeline_props) at same time,
  or MLB-only for POC then port when validated? Simpler = MLB-only first.
- **Q2**: The 12 legacy score functions have decade-of-baseball-knowledge.
  Which get ported as plug-in signals vs left as legacy scoring? Rough
  cut: port the SIGNALS (heaters, matchup edges, weather) but keep the
  base tier/conviction FROM legacy as an aggregate signal chip.
- **Q3**: Shadow decision storage — is `mlb_pipeline_props.playbook_*`
  columns enough, or do we want a separate `prop_playbook_decisions`
  table for cleaner history?

## Estimated scope

- Design + schema + 3 seed signals + scorer skeleton: 4-6 hours (today)
- Full port of legacy scoring signals to playbook: 2-3 days
- Shadow validation period: 14 days minimum before cutover
- **Realistic launch-ready timeline: 3 weeks from today (2026-09-07)**

Related: [[project-prop-playbook-port-817]] (why),
[[project-jerry-vs-sharp-card-817]] (game analog),
[[feedback-batter-hits-juice-trap-803]] (proven pattern to seed)
