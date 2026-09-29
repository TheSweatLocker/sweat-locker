---
name: project_pricing_launch_decision
description: Launch pricing locked 2026-06-16. $14.99/mo + $119.99/yr (33% annual discount) + 7-day free trial. Free tier explicitly NO Jerry. NO launch discount — trial is the lever. NFL Phase 2 moved up from August to July for fall retention.
metadata:
  type: project
---

**Decisions locked 2026-06-16 (combined launch + pricing session):**

## Pricing
- **Paid tier: $14.99/month or $119.99/year** (33% annual discount, $9.99 effective monthly)
- **7-day free trial** on both — Apple-native via App Store Connect
- **NO launch discount** — trial is sufficient conversion lever; pricing not diluted
- Already entered in App Store Connect at $14.99 — confirmed keep (avoids rework + positions premium correctly)

## Free tier scope
Free users get:
- POTD pick (label + line + Sweat Score)
- Today's slate visibility (game cards + scores)
- 7-day pick history view
- **NO Jerry narrative** (personality + voice is paid moat)
- **NO cohort drivers / hit rates** (signal moat is paid)
- **NO prop card** (props paid)
- **NO sweat card / full daily card** (paid)

## Paid tier value
- Full daily card (5-7 picks)
- All prop tiers (PRIME / STRONG / LEAN)
- Cohort drivers + live hit rates
- ALL active sports (MLB → NFL → NCAAB → NCAAF → NBA → NHL as season-active)
- Full Jerry deep reads + game narratives
- 30-day Sweat Card history
- Sweat Locker Ladder
- Push notifications
- Multi-leg parlay builder

## Comp set positioning
| App | Price | Notes |
|-----|-------|-------|
| Action Network PRO | $8.99/mo | Single sport at a time, no proprietary model |
| BettingPros | $19.99/mo | Single-source picks |
| Outlier Pro | $24.99/mo | EV calculator only |
| OddsJam | $99/mo | Sharp tools |
| **Sweat Locker** | **$14.99/mo** | 4-5 sports + proprietary cohort engine + Jerry + sweat scoring + ladder |

Sweat Locker priced at floor of "premium analytics" and above "casual picks" — exactly where proprietary multi-sport product belongs.

## NFL Phase 2 — moved up
- **Previous plan:** August 2026
- **New plan:** July 2026 (parallel with MLB launch consolidation)
- **Reason:** NFL solid at Sept 5 kickoff = fall retention engine. User's read: MLB gets initial paid users, NFL retains them. If NFL launches half-built, churn through fall is catastrophic.

## Revised offseason calendar
| Month | Workstreams |
|-------|-------------|
| July | MLB launch (post-July 1) + NFL Phase 2 cohort engine port + tier scoring + NBA model recal start |
| August | NFL Phase 2 finish + pre-season validation + NCAAF v1.0 + NBA cohort engine port |
| September | NFL launch (Week 1) + NCAAF launch + NHL Phase A + NBA prop pipeline build |
| October | NBA preseason model recal + NBA v2.0 launch with FULL prop pipeline + NHL launch |

User committed to working hard to hit NBA Oct 1 with full prop pipeline parity (originally proposed slip to mid-Nov v1.1).

## NFL launch framing (compliance + expectation setting)
- "Same proprietary engine that produced [MLB record] now applied to NFL with a calibration window through Week 4"
- Don't claim "tier proven" until live forward-test data backs it
- Position pre-season + Weeks 1-4 as engine's *calibration phase*
- By Week 5+ live posterior data backs the tier labels

## Risk surface
Doing NFL Phase 2 + NBA full rebuild + NCAAF + NHL across Jul-Oct is aggressive. Mitigations:
- Universal cohort architecture: write engine port once, apply features per sport
- Be ruthless about scope (no new framework experiments — production patterns only)
- Time-box hard
- NCAAF + NHL stay strictly Spread/ML/Total — NO props v1.0 (already decided)
- NBA prop pipeline is the wild card — slip to mid-Oct if NFL or NCAAF demands attention

Related: [[project_unified_taxonomy_decision]] [[project_nba_offseason_rebuild]] [[project_ncaaf_scope]] [[project_nhl_scope]] [[project_launch_may_week1]]
