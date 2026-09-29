---
name: project-sweat-card-multi-sport-902
description: 9/2 spec for Sweat Card multi-sport blend — cross-sport conviction rank + sport-in-season priority + 5-cap peak days + user filter. Post-launch build.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T19:14:43.667Z
---

🎯 **9/2 Sweat Card multi-sport blend — full spec locked, build queued
post-launch.** MLB-only today. NFL Sunday kicks off 9/7 (post-submit)
so this doesn't block launch, but it's Week 1 imperative.

**Why:** Currently `generate_sweat_card.py` reads only MLB context.
When NFL Sunday hits, users on the Sweat Card won't see NFL picks.
Bad UX for our biggest slate day.

**How to apply:** Extend the composition script to blend cross-sport
picks. Do NOT touch pre-launch — build first week of post-launch.

## Composition rules (all confirmed 9/2)

**Backbone: pure conviction ranking (option B)** across all active
sports. Rank primary_play by conviction score, take top N.

**5-cap on multi-sport peak days:**
- Sat: CFB + MLB active → 5 cards
- Sun: NFL + MLB active → 5 cards
- Other days: 4 cards default

**Sport-in-season priority tiebreak (option C for Q3):**
- Football priority Sept-Jan (NFL/NCAAF beats MLB)
- MLB priority Apr-Sep (before football starts)
- NBA/NCAAB/NHL priority Nov-Apr

**Early-season football tier cap (Q2):**
- NFL/NCAAF Weeks 1-3 capped at STRONG max on Sweat Card
- Even if scorer says PRIME, display as STRONG
- Auto-lifts when Week 4 sample calibrates

**User-selectable sport filter (option D):**
- Add sport-filter chips at top of Sweat Card
- Filter existing card composition, don't re-fetch
- "All" default, single-sport isolates to that sport's picks

**5th slot content (Q4):** Same conviction ranking — no diversity
forcing. If the 5th-best is another MLB total, that's the pick.
Honest > forced diversity.

## Data plumbing

**All picks come from same underlying stack:**
- `{sport}_game_context.primary_play` for tier/label/conviction/side
- `_ensemble_sources` array for signals contribution
- `jerry_cache` for LLM narrative (MLB daily, NFL Thu-lock)
- Same per-tab UI reads for slots 1-4 works for slot 5
- Sport emoji header per card so users see "🏈 NFL" or "⚾ MLB" at a glance

## Implementation shape (post-launch build)

1. `generate_sweat_card.py`:
   - New MULTI_SPORT_PEAK_DAYS config `{'Sat': 5, 'Sun': 5, else: 4}`
   - Fetch primary_plays cross-sport (currently only reads MLB)
   - Sport-in-season priority weight map by month
   - Early-season football tier cap logic
   - Same MAX_PICKS_PER_GAME + PROP_TYPE_DIVERSITY caps apply

2. App side (`app/index.tsx` Sweat Card render):
   - Sport filter chips at top
   - Sport emoji per card header
   - Card count adapts to composition (4-5)

## Timeline

- Pre-launch: NOT touching. MLB-only ships fine.
- Week 1 (9/7-13): Build + test multi-sport composition
- Week 2 (9/14-20): Deploy — first Sunday with NFL/MLB blend
- Week 3+: monitor + tune based on user feedback

## Cross-references
- [[feedback-card-process-discipline-718]] — cap at 4, sides>totals
- [[project-vault-match-901]] — Vault badges also render per-card
- [[project-nfl-jerry-thu-lock-902]] — NFL Thu-lock provides stable Jerry reads
- [[project-nfl-ncaaf-week1-readiness-820]] — Week 1-3 tier cap discipline
