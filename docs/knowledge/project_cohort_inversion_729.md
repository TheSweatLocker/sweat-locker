---
name: cohort-inversion-729
description: "Lifetime audit 7/29 found h2h_recent_home cohort hits 31.1% (n=45) — 68.9% inverse. bp_taxed 42.4% n=99 + trend 46.3% n=82 also fade signals. Fix spec: invert h2h_recent_home vote sign, downweight/kill bp_taxed + trend, upweight park/pitcher_vs_team/xera."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-29T20:27:58.156Z
---

**Set 2026-07-29 after full-lifetime cohort audit (n=210 recent games in game_context window).**

## The finding

Cohort win rates when they fire (min n=20):

**Winners (keep + upweight):**
- park: 62.5% (n=32) ⭐
- pitcher_vs_team: 58.1% (n=31) ⭐
- wrc_hand: 57.1% (n=35)
- xera: 55.9% (n=118)
- bullpen: 55.1% (n=107)
- ops_l14_heat: 54.8% (n=104)

**Traps (invert or kill):**
- **h2h_recent_home: 31.1% (n=45)** — 🚨 inverse hits 68.9%
- bp_taxed: 42.4% (n=99)
- trend: 46.3% (n=82)

## Why this matters

**h2h_recent_home** is the most-broken signal in the entire pipeline right now. When it fires HOME lean, HOME loses 68.9% of the time. When it fires AWAY lean, AWAY loses 68% of the time. Both directions fire against the actual winner — this is a systematic inversion.

Root cause hypothesis: H2H recent history is capturing what already happened (public books priced it in) rather than predicting forward. The "team just beat this opponent" signal is a mean-reversion trap — books adjust, market fades, cohort keeps voting the wrong direction.

**bp_taxed** is the "bullpen recently overworked" fade signal. 42% hit rate means recently-taxed BPs actually perform BETTER than the cohort predicts — probably because managers rest arms proactively.

**trend** (46%) is the "team-form momentum" signal. Not quite a full inversion but well below 50% — momentum cohort is anti-EV.

## Fix spec

1. **Invert h2h_recent_home sign in confluence_signal_computation:**
   - When h2h_recent_home says "home" → vote AWAY
   - When h2h_recent_home says "away" → vote HOME
   - Log this as an audit note in signal_confluence_breakdown so we can validate the inversion works over next 45 games (n=45 threshold matches current sample)

2. **Downweight bp_taxed to 0.5x** (or remove entirely if implementation is easier)

3. **Downweight trend to 0.5x** (or remove)

4. **Upweight park + pitcher_vs_team to 1.5x** (both ≥58% at n≥30)

## How to apply

- Fix candidate: `mlb_pipeline/signal_confluence.py` (or wherever cohort votes get tallied into signal_confluence_net)
- Track: Add a `signal_confluence_shadow_breakdown` column that stores BOTH old and new vote sums so we can A/B compare over next 4 weeks
- Grade: nightly compare shadow vs live confluence net across ML hit rate
- Promote inversion to live once shadow shows ≥+8pt lift at n≥30

## Related
- [[project_dynamic_cohort_framework_607]] — dynamic cohort recomputation
- [[project_cohort_engine_universal_architecture]] — moat architecture
- [[project_cohort_combos_721]] — SPLIT-LEAN cohort insights
- [[project_confluence_net3_trap_729]] — sister finding on |net|=3 trap zone
