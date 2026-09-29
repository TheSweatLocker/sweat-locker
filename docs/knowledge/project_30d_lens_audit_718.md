---
name: 30d-lens-audit-july-18-2026
description: "Honest 30d audit (n=359) after 7/17 bad night. v4 broken on totals (46%), Panel UNDER-biased, gate ELITE tier 8-0. Shipped v4 drop + jerry debias."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Ran 2026-07-18 after 7/17 card went 1-4 with 1 push.** Post-mortem revealed my "v3 hit 71%" claim was 1-night tail variance. Real 30d picture is much more sober.

## 30-day lens hit rates (n=359 graded games)

| Lens | Total hit% | Side hit% |
|---|---|---|
| v3 | 52.7% (n=237) | 57.0% (n=272) |
| v4 | **46.1%** (n=267) ⚠️ | 54.1% (n=283) |
| jerry raw | 52.1% (n=263) | 55.6% (n=302) |
| jerry-debiased (−0.62) | **53.6%** (n=274) | – |
| Composite baseline | 50.2% (n=269) | – |
| Composite (v3 + jerry-debias, drop v4) | **56.1% ⭐** (n=244) | – |
| Panel | 50.0% (n=184) | **58.5%** (n=188) ⭐ |

## Directional bias (30d)

Actual OVER rate on the sample: **47%**

| Lens | OVER call % | Bias |
|---|---|---|
| v3 | 83.3% | Heavy OVER |
| v4 | 88.5% | Heavy OVER |
| jerry | 70.4% | Moderate OVER |
| Panel | 33.2% | Heavy UNDER (**-14pp from actual**) |

## What I shipped

1. **Jerry debias (SHA 2e8fbf9):** subtract 0.62 from jerry_total in composite calc + direction counting. Modest but real +1.2pp Composite lift.
2. **v4 drop from Composite total calc (SHA 3e61ab0):** v4 is 46% on totals over 30d, dragging Composite down. Removing v4 from composite_avg while keeping it in _models_direction (consensus counting) lifted the raw composite signal to 56.1%.

## Gate-level finding

Full gate performance (30d, n=62 published): **46.8%**
- **ELITE tier: 8-0 (100%)** ⭐ — stricter criteria work
- STRONG tier: 3-8 (27%) — broken
- LEAN tier: 16-23 (41%) — subpar

**Gate is DEGRADING the raw composite signal** because the Panel-disagree flip publishes too many LEAN UNDERs. Panel is 33% OVER-biased vs 47% actual = Panel systematically over-calls UNDER, and on OVER-heavy nights (like 7/17 with 5 blowouts) those flips get destroyed.

**Actionable:** trust ELITE-tier gate outputs, downgrade STRONG/LEAN significantly until gate tier rewrite ships.

## What I claimed on 7/17 that DIDN'T survive 30d audit

- ❌ "v3 hit 71%" — was one night. 30d is 52.7%. Tail variance.
- ❌ "v4 hit 75% on sides" — was one night. 30d is 54.1%. Coin flip.
- ❌ "Panel is alpha" — 50% on totals over 30d. Coin flip.
- ❌ "Weight v3 heavier for totals" — no 30d evidence.

## What DID survive

- ✅ v4 broken on totals (46.1% below coinflip)
- ✅ Panel UNDER-biased (33% OVER calls vs 47% actual)
- ✅ Panel best on sides (58.5% — leads all lenses)
- ✅ Jerry debias helps modestly (+1.2pp Composite)

## Gate rewrite queued (not shipped)

STRONG and LEAN tiers need reweighting. Panel-disagree flip is too permissive on OVER-heavy nights. Full gate rewrite is a dedicated project, not a same-day patch.

**Interim:** for card recommendations, only trust ELITE-tier gate outputs at full conviction. Downgrade STRONG to 6/10 max, LEAN to 5/10.

## Related

- [[project_composite_debias_finding_712]] — original July 12 debias finding
- [[project_confluence_dead_signal_712]] — same "audit dead heuristics" pattern
- [[feedback_confidence_in_first_pass]] — honest confidence rule
- [[feedback_verify_player_team_first]] — verify-first process pattern
