---
name: project_mlb_prop_board_collapse_926
description: "MLB prop board publishes from the last 1% of the pipeline, so every day looks like a new bug"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-26T14:45:36.611Z
---

Andy 2026-09-26: "can you not fix the mlb prop pipeline, its different
problem everyday, today barely any props."

He is right that it is different every day, and the reason is structural:
**we publish from the residue of the pipeline, so any small variation swings
the published count wildly.**

Published props/day: 101 · 18 · 97 · 75 · 65 · 58 · **5** (09-20 → 09-26)
with the board size flat at ~1,400 the whole time.

## The funnel on 2026-09-26

| stage | count |
|---|---|
| generated | 1,377 |
| on families banned at every tier | **1,250 (91%)** |
| eligible | 127 |
| of those, `_coverage_stub` (no real data → unscoreable) | 84 (66%) |
| scored (conviction > 0) | **18** |
| published | **5** |

Compare 09-22: eligible 344, scored 172 (50%), published 97. The eligible
set shrank 344 → 127 AND the scoring rate inside it fell 50% → 14%.

## Three separate causes, not one bug

1. **Generation is not aligned with the ban policy.** `prop_ban_policy`
   bans hr_over/under, total_bases, rbis, runs, hits_over at every tier —
   that is ~1,250 of today's rows. They are generated deliberately so the
   "should we un-ban this family?" retro has data, which is defensible, but
   it means 91% of the board can never publish.

2. **Batter enrichment was removed 2026-09-19** by commit `3021741b`
   ("two-speed prop pipeline: stop enriching rows that cannot publish").
   Batter conviction went 1,224 non-zero on 09-18 to **exactly 0** on 09-20
   and every day since. This is BY DESIGN — those families are banned — but
   it permanently caps the board at pitcher props.

3. **Coverage stubs dominate what is left.** 84 of 127 eligible props are
   `_coverage_stub` rows from `sweep_prop_coverage`, created before lineups
   post. Stubs carry no real data so they cannot score. See
   [[project_mlb_prop_stub_ceiling_925]].

`hits_under` alone fell 162 → 24, the single biggest driver of the
eligible-set collapse.

**Why:** conviction requires L5/L10 enrichment; enrichment only runs on
non-banned families; tiering requires conviction. Measured exactly on
09-26: 18 props had an L5 signal and 18 had conviction > 0. Same rows.

**How to apply:** do not patch the symptom — the coverage-kill gate is
correctly refusing unscored props, and demoting it would publish garbage.
The two real levers are (a) align generation with the ban policy, or
un-ban families on evidence, and (b) run prop generation AFTER lineups so
eligible rows are real rather than stubs. Until one of those moves, the
board stays in single digits on thin slates. Related:
[[project_prop_l5_leak_922]], [[feedback_prop_family_ban_three_layer]].
