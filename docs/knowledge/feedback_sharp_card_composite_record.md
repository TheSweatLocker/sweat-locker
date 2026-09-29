---
name: feedback-sharp-card-composite-record
description: 🎯 Sharp Card record = surface_records.sharp + surface_records.prop combined (sides+props). Not just .sharp alone. .sharp is jerry_reads sides-only.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-04T12:20:11.341Z
---

**When asked about "Sharp Card" or "Sharp tab" record, always report the COMPOSITE of `surface_records.sharp` + `surface_records.prop` — never just `.sharp` alone.**

## Why

`surface_records.sharp` (58W-41L-4P +11.72u at 8/20 epoch) is **sides only** — sourced from `jerry_reads` conviction≥60, cross-sport, flat 1u @ -110. It does NOT include prop picks.

The app's actual Sharp Card record display (app/index.tsx:9020-9040) sums `.sharp + .prop` at epoch to produce the number users see. As of 9/3:
- sides: 58-41-4 (+11.72u)
- props: 119-50 (+73.05u)
- **DISPLAYED: 177-91-4, +84.77u**

Reporting only `.sharp` (+11.72u) understates the Sharp Card by ~7x.

## How to apply

- Any "how did the Sharp Card do?" question → sum sharp + prop surface_records rows at the same window_key (usually epoch).
- The `daily_surface_records.sharp_card` per-day rows are the CORRECT per-day tally (28-10-1 +13.98u on 9/3). Use those for single-day answers.
- Sub-tabs of the Steam Room have their own surfaces: potd, ladder, ledger_chalk_parlay, ledger_teaser, ledger_teased_spreads, split_sharp_triple, dawg_of_day, daily_degen. Each has its own daily_surface_records row.

## Two "sharp" concepts to keep straight

| Name | Source | Contents | Where used |
|---|---|---|---|
| `surface_records.sharp` | jerry_reads conviction≥60 | Sides only, all sports | Rolled up into app Sharp Card composite (with .prop) |
| `daily_surface_records.sharp_card` | primary_play tier PRIME/STRONG + props tier PRIME/STRONG | Actual Sharp Card items per day | Per-day record tallies |

Rename one post-launch to prevent this same confusion.

## Also worth surfacing

Sharp Card cache blob (`jerry_cache.sharp_card_YYYY-MM-DD`) had 27 items on 9/3 but `daily_surface_records.sharp_card` logged 39 picks. Cache is what renders in-app, aggregator pulls broader. They should reconcile. Post-launch investigation.

## Related

[[project_sweat_card_vs_sharp_card]] · [[project_jerry_vs_sharp_card_817]] · [[feedback_backside_dictates_app_renders]]
