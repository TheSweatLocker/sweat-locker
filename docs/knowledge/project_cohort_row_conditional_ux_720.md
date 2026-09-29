---
name: cohort-row-conditional-ux-720
description: "Cohort rows read as directional facts but are actually conditional edge-boosts (model lean × situational context). UX fix queued alongside PRIME tier confusion."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-20T19:37:18.179Z
---

**Set 2026-07-20 after user questioned "bp_taxed → STRONG_EDGE UNDER 72.7%" — reasonably asked why a taxed bullpen would suppress rather than add runs.**

## The finding

User's intuition was right for the naked cohort:
- home_bp_taxed alone: UNDER hit rate 50.7% (n=523) — coinflip
- bullpen_taxed_either: UNDER 51.3% (n=684) — coinflip
- neither_taxed: UNDER 49.1% (n=165) — coinflip
- **Book actually sets LOWER lines for taxed BP** (8.53 vs 8.67 neither) — book prices roster fatigue directly

But the cohort rule that showed 72.7% was:
```
v3_tot | home_bp_taxed | under | 72.7% (52-17 · 69g)
```

Which reads to a user as: "home_bp_taxed → UNDER 72.7%"

Actually means: **"When v3 has already leaned UNDER AND home_bp_taxed matches, the UNDER hits 72.7%."**

## Why the interaction works (not pricing inefficiency)

1. Book prices the roster fatigue in the line (accurate directional adjustment).
2. Book underprices the *behavioral response* — managers with taxed pens actively extend starters, sometimes past the point that pure matchup math would justify.
3. v3's UNDER lean was already selecting for SP strength. Layering the manager-behavior edge on top of an SP-strong base is where the 72.7% comes from.

So it's a **behavioral edge stacked on a model-selection edge**, not a naked pricing inefficiency.

## UX problem

Every cohort row currently displays as:
```
cohort_name | tier | direction | shrunken% | raw record
```

A user reads this as a standalone directional fact. Actually every row is a conditional:
```
When [model] already leans [direction], [cohort_name] boosts hit rate to X%
```

## Fix options (recommended path first)

1. **Restructure the row** to show the conditional up-front:
   - Current: `home_bp_taxed | STRONG_EDGE | under | 72.7%`
   - New: `v3 UNDER lean × home_bp_taxed → 72.7%`
   - Groups the play_type + cohort as one line, makes the interaction visible

2. **Add a header row per play_type** in the cohort section:
   - Group by play_type (already done in UI), but relabel header to "When v3 calls UNDER, these cohorts boost:"
   - Makes it explicit these rules are conditional filters

3. **Tooltip on hover** explaining conditional nature
   - Weakest fix — hover doesn't work on mobile, users might not discover it

## Recommendation

**Option 1 for launch.** Restructure the cohort row so the interaction is the primary read, not a footnote. Same data, clearer semantics.

## Related

- [[project_prop_tier_ux_confusion_720]] — earlier UX confusion about PRIME 68 badge
- [[project_dynamic_cohort_framework_607]] — cohort framework itself is sound; only the display needs work
- [[project_launch_priorities_july]] — both cohort UX issues are pre-launch fixes
