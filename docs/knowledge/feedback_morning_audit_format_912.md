---
name: feedback-morning-audit-format-912
description: "🚨 DEFINITIVE morning audit format. Same structure every morning: Sweat Card → POTD → Jerry game picks per sport → Prop pipeline → each Steam Room tab. Correct names: Sweat Card (NOT Sharp Card), The Sharp (NOT Sharp Card), The Split, The Ladder, The Ledger. Always flag ungraded counts."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-12T11:38:58.790Z
---

# Morning audit format — canonical

Andy corrected me 2026-09-12 morning: every morning I drift on surface
names and structure. The audit runs the same way every day. Commit to
this format and don't reinvent it.

## The five surfaces, in order

Every morning audit runs these five sections, in this order, using
these exact names:

1. **Sweat Card** — the daily main dashboard (8 curated picks + POTD +
   Dawg of the Day + Rest of Today's Card + yesterday recap). Broader
   inventory across all sports, tiered PRIME / STRONG / LEAN / READ.
   NOT "Sharp Card." NOT the same thing as The Sharp.

2. **POTD** — Pick of the Day. Its own graded record surface. Andy has
   been vocal about POTD selection quality
   ([[project_potd_selection_redesign_908]]) — always call out the pick
   + why it was selected + result.

3. **Jerry game picks (in Analysis)** — every game's Jerry read on the
   game card. Grouped per sport: MLB, NFL, NCAAF, etc. Show win rate
   AND ungraded count. Source: `jerry_reads` table.

4. **Prop pipeline** — the tiered prop system: PRIME / STRONG / LEAN /
   COVERAGE. Show W-L-P per tier AND ungraded count per tier. Source:
   `mlb_pipeline_props` table (MLB) plus other-sport equivalents where
   they exist.

5. **Steam Room P/L per tab** — the four Steam Room sub-tabs, in this
   canonical order (per [[feedback_steam_room_tab_names]]):
   - **📊 The Split** — sharp $ vs public bets (display surface — no
     P/L record; skip unless something looks off)
   - **🪜 The Ladder** — 1 pick/day compounding streak. Surface key
     `ladder` in `daily_surface_records`.
   - **🎯 The Sharp** — disciplined sharp-money list. Surface key
     `sharp` internally. Composite: `surface_records.sharp` +
     `surface_records.prop` combined per
     [[feedback_sharp_card_composite_record]].
   - **🧾 The Ledger** — parlays + teasers. Surface keys
     `ledger_chalk_parlay`, `ledger_teaser`, `ledger_teased_totals`,
     `ledger_teased_spreads`, `ledger_chalk_prop_parlay` in
     `daily_surface_records`.

## Never do these three things again

- **Do NOT call anything "Sharp Card" in user-facing framing.** The
  Steam Room tab is **The Sharp**. Internal DB has `surface='sharp'` —
  that's fine for code, never in a report label.
- **Do NOT confuse Sweat Card with The Sharp.** Sweat Card = dashboard
  breadth; The Sharp = disciplined slice inside Steam Room. Different
  surfaces, different P/L, different discipline rules.
- **Do NOT report the graded win rate without the ungraded count.**
  E.g., "PRIME props 11-1" was misleading 9/12 — 45 of 57 were still
  ungraded. Always show `W-L (n graded of N total)` or the full
  ungraded count separately.

## Why

Andy: "every morning i ask the same thing … and every morning you do
something different." Same structure every day, same names every day,
same ungraded transparency every day. This is a trust surface: if the
morning audit drifts on structure or overstates records via graded-only
counts, the whole automation looks unreliable.

## How to apply

Every time Andy asks for the morning audit (recognized phrases:
"morning audit", "yesterday recap", "how did we do", "get the audit
going"):

1. Pull data for the five surfaces above, in that order
2. Use ONLY the canonical names in report headers and prose
3. For every graded record, also show ungraded count if > 0
4. If a surface has 0 rows for a sport (season not started etc.),
   say so explicitly rather than omitting the header

Related: [[feedback_sweat_card_vs_sharp_card]],
[[feedback_steam_room_tab_names]],
[[feedback_sharp_card_composite_record]],
[[feedback_daily_yesterday_recap]].
