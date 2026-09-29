---
name: project-nfl-launch-plan-805
description: NFL prop calibration launch — cross-season aggregation + lower MIN_N=10. Approach ratified 2026-08-05 for Sept 4 opener.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-06T02:32:03.571Z
---

NFL prop pipeline gets the [[project-calibration-architecture-805]] framework by Sept 4 opener. Key adaptations for the sample-size gap vs MLB:

**Sample math:** MLB = 15 games × 30 players × 5 markets/day = ~2,000 rows/day. NFL = 16 games/week × 12 players × 3 markets = ~600/week. NFL hits `MIN_N_ACTIONABLE=15` ~30× slower than MLB.

**Two mitigations (user ratified 8/5):**
1. **Cross-season aggregation** — seed NFL bucket_roi + FAMILY_BASE_RATES from 2024 backfill + partial 2025. Rolls forward into 2026 as new grades accumulate. `_refresh_from_live_data()` pools across seasons naturally.
2. **Lower `MIN_N_ACTIONABLE` for NFL to n=10** — accept slightly more noise to get earlier trap-pattern signal. MLB stays at n=15.

**Sequencing:**
- Now → mid-Aug: MLB drinks from calibration fire hose. Grade rules perform, refine.
- Late-Aug: Wire `compute_nfl_prop_bucket_roi.py` on 2024 backfill. Seed NFL family priors + fade combos.
- Sept 4 opener: NFL prop pipeline runs same calibration framework, rules pre-seeded from 2024 data.
- Ongoing: `_refresh_from_live_data()` drifts NFL rules with 2026 grades on top.

**Sport-specific work needed:**
- NFL signal parsers (analogs to L3 ERA / xERA / L14 wRC+): `pass_yds_L3_avg`, `target_share_L4`, `snap_pct_L3` — TBD from actual signals stored.
- NFL family name dictionary entries in FAMILY_BASE_RATES (pass_yds_over/under, rush_yds_over/under, receptions_over/under, etc.).
- NFL-specific `FAIR_PRICE_TIER_FADES` entries — will emerge from 2024 backfill audit before opener.

**Don't:** ship MLB fade rules against NFL prop_types assuming they transfer. Each sport discovers its own trap patterns.
