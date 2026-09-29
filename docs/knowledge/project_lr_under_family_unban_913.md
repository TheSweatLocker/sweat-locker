---
name: lr-under-family-unban-913
description: v1.0.1 opportunity — un-ban 4 batter UNDER families that LR shadow is calling at 60-91% hit; 467 hidden wins/30d
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-13T16:37:27.182Z
---

**Queued for v1.0.1** (Andy 2026-09-13: wait until v1.0.1 because tabs need to be added for these families).

## The opportunity

LR shadow 30-day track record on the 4 currently-banned UNDER families:

| Family | Promoted | Record | Hit% |
|---|---|---|---|
| hr_under | 207 | 149-15 | **90.9%** |
| rbis_under | 183 | 104-46 | **69.3%** |
| total_bases_under | 217 | 116-66 | **63.7%** |
| runs_under | 196 | 98-64 | **60.5%** |

Total: **467 wins hidden over 30 days** just from these 4 families.

## Why they were banned (context)

Commit `a8a984df` (9/12): Andy directive after seeing surprise RBI plays
on launch day. "we dont even list rbi in prop jerry i have actually
never seen rbis plays until today on our first fucking full launch
day." Ban was correct at the time because BOTH sides (OVER + UNDER)
were surfacing and OVER was a trainwreck (rbis_over 31%, hr_over 12.6%,
total_bases_over 32.4%, runs_over 45.7%).

## The insight

The ban lumps both sides. UNDER side is where LR quietly prints.
Selective un-ban of ONLY UNDER preserves the UX intent (no surprise
NEW family types) while unlocking the LR edge.

## v1.0.1 checklist

1. **UI first (blocker per Andy):** Add tabs / rendering for the 4
   families in Prop Jerry (and Sweat Card if they surface there):
   - HR Under
   - RBI Under
   - Total Bases Under
   - Runs Under
   Design: could reuse existing pitcher-prop tab pattern; may need
   an "Alt Batter" section or dedicated new tab group.

2. **Backend un-ban:**
   - `generate_prop_jerry_synthesis.py` `_MLB_BANNED_PROP_TYPES`: drop
     `hr_under`, `rbis_under`, `total_bases_under`, `runs_under` from
     the set. Keep all `_over` variants + `batter_ks_*` + `hits_under`
     banned.
   - `prop_ensemble_scorer.py` `_NEVER_PUBLISH_TYPES`: same change.
   - `backfill_prop_lookback.py` MLB_STAT_MAP: add `total_bases`,
     `rbis`, `runs`, `hr` entries so L5/L10 graphs render (per
     project_mlb_prop_l5_l10_gap_912 requirements).
   - `backfill_prop_lookback.py` `_MLB_API_STAT`: add matching
     (api_group, api_field) tuples for MLB Stats API pulls.

3. **Regression watch:** run 7-day rolling on the 4 families for
   first 2 weeks post-launch. If any drops below 55% hit rate, re-ban.

## Do NOT un-ban

Keep permanent-banned:
- hr_over (12.6% — disaster)
- rbis_over (31.0%)
- total_bases_over (32.4%)
- runs_over (45.7%)
- batter_ks_over (0% n=4)
- batter_ks_under (100% n=5 — sample too small; hold)
- hits_under (previously flagged as loser)

## Sizing the opportunity

Current 30d prop cohort: 664-211 (75.9%) at +302u.
Adding 467 wins at ~68% blended → +150-200u/30d additional (rough).
Total potential: ~500u/30d prop cohort.

Related: [[project_lr_shadow_stale_909]]
