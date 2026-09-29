---
name: hits-ban-verdict-917
description: "hits_over + hits_under stay BANNED at ALL tiers (2026-09-17). PRIME record artifacts, not real edge. Do not re-litigate without richer composition data."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T18:06:31.324Z
---

**Decision (2026-09-17 evening):** `hits_over` and `hits_under` remain
banned at all tiers via migration 20260917b (Rule 4b + Rule 5 + Rule 6).
Do not un-ban without a proper edge analysis.

**Why:** aggregate 30d record for hits PRIMEs LOOKS good but composition
audit shows the numbers are artifacts:

- **hits_under PRIME 18-0 (100%)** — all 19 rows on just 2 dates (9/12 +
  9/14). Almost every line is Under 0.5. Players are September call-ups
  / bench guys (Ryan Waldschmidt at -600, Tyler Tolbert, Owen Caissie,
  Christian Moore, Brock Rodden, Carlos Jorge, Lazaro Montes, Michael
  Arroyo). At -600 juice on bench guys, we're picking "player might not
  even bat" — grades Win if they sit or get one AB. Not real skill.
- **hits_over PRIME 38-14 (73%)** — 61 rows over only 6 unique dates
  (concentrated, not distributed). All at line 0.5. 28/61 at -200+ juice
  trap. 9 voids (15% void rate). Real ROI math: 38 wins × avg -180 =
  +21u, 14 losses × -1u = -14u, net +7u on 52 wagers = +0.14u/pick
  average. Positive but marginal, mostly on trap juice.

**How to apply:** if hits PRIMEs re-surface in a future audit and someone
proposes un-banning them again, run the composition check first before
tier-gating:

1. Date-diversity: count unique game_dates in the sample. If <10 across
   30d, the "edge" is likely a clustered pattern, not durable.
2. Juice profile: bucket book odds (`<= -200 trap` / `-150..-199` /
   `-110..-149` / `+100+`). If >30% at -200+, the aggregate hit% doesn't
   translate to +ROI.
3. Line variety: count distinct prop_line values. If only one line (0.5
   for hits), the market is essentially binary — thinner edge than
   pitcher props with real projection deltas.
4. Void rate: hits Under 0.5 for bench players grades Void when the
   player doesn't play. Void rate >10% means we're picking non-players
   who "safely" don't get to bat.
5. Player composition: eyeball the roster. If >50% are September
   call-ups / rookies with <100 PA on the season, sample is
   September-only artifact.

**Rec threshold to un-ban:** all four gates pass on a fresh 30d slice
AND real ROI (juice-adjusted) beats +0.30u/pick. Prior 20260917c
migration file was deleted 2026-09-17 evening after Andy called out
the 18-0 as "can't be right" — verification confirmed.

**Related:**
- [[feedback_batter_hits_juice_trap_803]] — original Hits O 0.5 juice
  trap discipline
- [[feedback_publishable_view_drift]] — the general drift discipline
  for any migration touching v_mlb_props_publishable
