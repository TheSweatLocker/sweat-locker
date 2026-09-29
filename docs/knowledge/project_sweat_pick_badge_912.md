---
name: project-sweat-pick-badge-912
description: "Spec for high-conviction \"Sweat Pick\" badge on game cards for slate-heavy sports (NCAAF, NCAAB); not yet built"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-13T00:45:47.283Z
---

**Fact:** Andy 9/12 signed off on a spec for a rare gold-tier badge called **⭐ SWEAT PICK** that appears on the top 3-5 model-backed games per slate. Purpose: give users a fast scroll-target on slate-heavy sports (NCAAF 55+ picks/Sat, NCAAB ~150 games/night when it launches Nov 3) so they don't have to read every card to find our highest-conviction plays.

**Why:** Tier chips (PRIME/STRONG/LEAN) work fine when there are 12 NFL games, but drown users on 55+ NCAAF games. A single distinctive badge shown on ~3-5 games per slate gives a scannable "these ones first" signal. Andy asked for "no lock language" — picked SWEAT PICK over HOT READ / MODEL BACK / THE READ / HIGH CONVICTION.

**How to apply:** Not built yet. When Andy greenlights implementation:

**Trigger — SIMPLIFIED per Andy 9/12:** badge = every pick that lands on the sport's CARD record surface (`ncaaf_card` / `nfl_card` / `nba_card` / etc.). Rationale from Andy: "whatever picks are being recorded — ideally those are the best right?" The card record is already the curated top-conviction slice; tying the badge to it means no separate thresholds to tune, no drift risk, and the badge inherently means "this is a pick we stake our record on."

**In code terms:**
- Read from `daily_surface_records.detail[]` for the sport's card surface (e.g. `ncaaf_card`), or
- Compute at ctx-write time: `primary_play.tier in ('PRIME', 'STRONG')` AND `primary_play._manual_override` is null (pure model; overrides shown as 🚩 with their own visual instead)

**Practical thresholds (fallback if the above doesn't return N):**
- Enabled sport list only
- Max 5-8 per sport per day (natural cap from card curation logic)

**Storage:** inside existing `primary_play` JSONB — no migration.
```
primary_play.badge = "sweat_pick"   |   null
primary_play.badge_reason = "PRIME 78 + 4-signal confluence + no override"
```

**Backend helper** (proposed location: `mlb_pipeline/sport_registry.py` or new `mlb_pipeline/badge_engine.py`):
```python
def compute_badge(pp: dict, ctx: dict) -> str | None:
    if pp.get('_manual_override'): return None
    if pp.get('tier') != 'PRIME': return None
    if (pp.get('conviction') or 0) < 75: return None
    if abs(ctx.get('signal_confluence_net') or 0) < 3: return None
    return 'sweat_pick'
```
Then post-loop cap enforcement (top 5 by conviction per sport per date).

**Client impl:**
- New `<SweatPickBadge>` component in `app/components/`
- Renders when `primary_play.badge == 'sweat_pick'`, else null
- Placed top-right of game card (list view + game detail header)
- Design: gold background, dark ink, star emoji + "SWEAT PICK"
- Tap → tooltip showing `badge_reason` so users know why

**Rollout phasing (Andy 9/12: ship both together):**
1. **NCAAF + NCAAB together** at NCAAB's Nov 3 launch — pairs the badge debut with a natural marketing moment (first NCAAB slate)
2. NFL/MLB — later, if user data shows badge value on slate-heavy sports first

**Threshold tuning:**
- If badge fires on >10 games in a day, tighten to conviction ≥78 + confluence ≥4
- If it fires on 0 games consistently, loosen to conviction ≥72

**Cross-refs:**
- Sibling to [[project_sweat_badges_901]] (badge system framework)
- Complements existing tier chips (PRIME/STRONG/LEAN) — sweat_pick is a SEPARATE overlay, not a replacement
- Tracking: manual overrides EXCLUDED from badge (see [[project_v1_0_1_client_priorities]] item on tracking manual overrides separately)

**Open questions:**
- Client-side placement in list view vs detail header — needs mockup
- Animation on badge appearance? (fade-in on scroll vs static)
- Whether to show badge in Sweat Card top_picks (probably yes since those are usually PRIME anyway)
