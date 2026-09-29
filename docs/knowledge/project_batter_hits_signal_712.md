---
name: batter-hits-over-is-signal-not-straight-bet
description: "30d batter hits_over PRIME 69% / STRONG 65% (n=459 total) is a SIGNAL surface, not a straight bet. Typical book juice (-300 to -400) makes it a losing bet even at 69% real hit rate."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Discovered 2026-07-12 during fix-round C ("formalize batter hits_over surface").**

## The data

30-day batter hits_over performance on internal 0.5 line (no book_line attached):

| Tier | W-L | Hit % | n |
|---|---|---|---|
| PRIME | 164-75 | **69%** | 239 |
| STRONG | 143-77 | **65%** | 220 |
| LEAN | 4-7 | 36% | 11 |

## Why it can't publish as a straight bet

Sportsbooks list "1+ hits" as an alt line at ML odds. Typical pricing:
- Elite regular: -280 to -320 (73-76% implied)
- Average regular: -220 to -280 (69-74% implied)
- Weaker regular: -160 to -220 (62-69% implied)
- Backup/rookie: -110 to -160 (52-62% implied)

At 69% PRIME real hit rate:
- vs -300 (75% implied) → losing bet
- vs -250 (71% implied) → slight loser
- vs -200 (67% implied) → +2% edge ✓
- vs -150 (60% implied) → +9% edge ⭐

So the signal is real but **only profitable for specific batters priced at -200 or better** — typically medium-tier regulars in specific matchups. Without per-batter book-odds integration, we can't route to only profitable subsets.

## How to apply

- **Don't publish batter hits_over as a straight-bet card play.** The card assumes standard ML odds attached; batter hits alt-line juice will silently eat the edge.
- **Use as a SIGNAL surface:** SGP legs correlated with team OVER totals (e.g., MIN OVER 9.5 + Jeffers 1+ hits is a natural correlated parlay), confirmation of pitcher HA-OVER props on opposing pitcher.
- **Card writeup framing:** Frame as "batter signals confirm the OVER thesis" not "here's a 69% straight bet."

## Queued follow-ups

- **UI relabel:** app currently displays "Over 0.5 Hits" (unclear). Rename to "1+ hits internal signal" or move to a distinct card section. See `app/index.tsx:9662, 10961`.
- **Prop class column:** add `prop_class = 'STRAIGHT_BET' | 'INTERNAL_SIGNAL'` to `mlb_pipeline_props`, tag hits_over 0.5 as INTERNAL_SIGNAL. Small migration + set logic in generate_props.py. Enables the app to render them under a separate section.
- **Book odds integration (out of scope):** if we ever add batter alt-line odds fetch (Odds API extra endpoint), can route directly-published straight bets to games where price is -200 or better.

## Related

- [[project_props_pipeline_pivot]] — original pipeline conversion from EV scanner
- [[project_prop_edge_calibration_july]] — proper KEEP/KILL bucket calibration (only applies to book-lined props)
- [[project_composite_debias_finding_712]] — same-day finding, similar theme: signal is real but sample-limited
