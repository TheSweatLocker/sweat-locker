---
name: external-transparency-differentiator
description: "External-picks tracking as core differentiator — graded results, rolling per-source calibration, consensus-fade alerts. Sharps+model agree + moderate public % is high-signal lane."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-22T23:13:57.987Z
---

**Set 2026-07-22 after vision discussion.**

## Product thesis

No competitor surfaces external handicapper picks with (a) attribution,
(b) graded track records, (c) fade-pattern alerts, (d) sharp-vs-public
divergence signals. Combined with proprietary model + prop pipeline,
this is what sets The Sweat Locker apart.

**Rule confirmed from 7/22 audit:** aggregate external consensus is
coinflip (53% n=13 on 7/21). High % + unanimous doesn't mean much.
The value is in *dissent patterns* + *sharp-vs-public divergence*, not
raw consensus.

## Signal lanes worth surfacing (research queue)

### Lane 1 — Sharps + model align, public moderate (55-65%)
The "quiet sharp play" — books haven't been forced to move by public
heat, but multiple trusted sharps + our model all say the same thing.
Public isn't loaded on it (so no reverse-line-move fade).

Hypothesis: this is the HIGHEST-signal external configuration. High
public % (75%+) typically already priced into the line; low public %
means sharps don't have consensus. Moderate public % + sharp+model
alignment = the sweet spot where edge remains.

**Audit target:** grade external picks × public bet % bucket × sharp
count agreement × model direction alignment. Look for the ≥60% hit
band.

### Lane 2 — Consensus fade (80%+ unanimous with audit-fade sources)
Hardcoded fade tags from 7/20: Pickswise 5-STAR, Ballpark Pal wind,
Action mid-gap +15-34. When 4+ books unanimous AND at least one is a
known fade source → auto-flag consensus_fade_alert on game_context.
App surfaces chip on card.

### Lane 3 — Dissent from consensus (contrarian source)
When 6 sources say HOME but 1 loud source says AWAY. Track that
dissenter's historical hit rate on dissent — some might be sharp
contrarians, some might be noise. Audit reveals.

## UX plan (3 tiers)

**Tier 1 (highest visibility):** Card-level "Consensus Fade Alert"
chip next to PRIME/STRONG badge when detector fires.

**Tier 2:** Promote ExternalPicksPanel from bottom of game detail
modal to right below Sweat Score card. Rename section framing to
"What Everyone Else Sees" so users understand why it matters.

**Tier 3 (v1.1 post 30d of data):** Dedicated "Sources" tab in bottom
nav — today's consensus heatmap + fade alerts per game.

## Concrete build queue

1. **resolve_externals.py + migration** (started 7/22 evening) —
   grades every external pick against sport game_results. Blocks all
   downstream analytics.
2. **external_source_calibration** table + nightly audit — rolling
   7d/30d/lifetime per (source × surface × sport). Feeds dynamic
   fade_flag refresh instead of hardcoded from 7/20.
3. **Consensus fade detector** — writes consensus_fade_flag +
   consensus_fade_note to mlb_game_context (and equivalents per sport).
   App reads flag → chip.
4. **Sharp-moderate-public audit** — n≥50 backtest on Lane 1
   hypothesis. Ship as project memory if edge confirmed.
5. **App-side Tier 1 + Tier 2** — badge on card + promote panel.
6. **Tier 3 tab** — after 30d graded data.

## Sport-agnostic scaling

All above sport-parameterized. `resolve_externals.py` takes
`--sport MLB/NFL/NCAAB`; calibration table has `sport` col;
consensus detector runs on any `<sport>_game_context`. MLB build
frees NFL + NCAAB automatically once their game_results populate.

## Legal

Nominative fair use holds as long as: attribution + link back + short
excerpt only + OUR fade tag labeled as OURS. All already in
ExternalPicksPanel component; carry pattern forward per source.

## Related

- [[project_external_aggregation_launch]] — original spec + 7/20 audit tiers
- [[project_contrarian_lens_dead_722]] — 60d audit: contrarian dissent = fade
- [[project_audit_721_full]] — 7/21 audit: external consensus 53% = coinflip
- [[feedback_everything_means_all_lenses]] — panel included in "all lenses"
