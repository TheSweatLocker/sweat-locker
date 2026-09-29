---
name: project-selection-grading-walkthrough-909
description: "🎯 Queued 9/9: full walkthrough of every surface's selection + grading process cross-sport (Sweat Card, POTD, Sharp Card, Ladder, Dawg, Daily Degen, Jerry Reads). Fail-in-one-doesn't-fail-everywhere design"
metadata:
  type: project
---

**Queued 9/9 by user.** Big discussion to have when we're both ready — user wants us on the same page for every surface's selection logic AND grading logic AND cross-sport implementation status.

**Scope — the 7 surfaces to walk:**

1. **Sweat Card** (home page) — how top_8 gets picked, tier lock (fixed 9/9), yesterday-recap "6-2" source
2. **POTD** (Play of the Day) — jerry_anchor_potd threshold logic, LR override, noPlay discipline
3. **Sharp Card** (Steam Room tab) — composition, conviction floor 73 (fixed 9/9), snapshot overlay (fixed 9/9)
4. **Ladder** (Steam Room tab) — dedicated ladder_state + ladder_rung, streak logic, rollup pattern
5. **Dawg of the Day** — daily_dawg selection, best contrarian dog play
6. **Daily Degen** — 4-6 leg lottery ticket, legs source, correlation checks
7. **Jerry Reads** (Games tab) — per-game synth for MLB, jerry_reads table, ensemble → primary_play → sync bridge

**For each, the walkthrough covers:**
- Selection algorithm: what data comes in, what gates fire, what emits
- Grading path: which resolver marks Win/Loss + unit_pnl
- Storage: which table(s), publish cache key if any
- Cross-sport status: which sports actually publish this surface (matrix)
- Failure modes we've hit + safeguards in place

**Key design principle to affirm (user's ask):**
"If we fail in one place we don't fail everywhere" — surfaces should be independently sourced so a Sharp Card bug can't take down POTD, POTD gap can't kill Jerry Reads, etc. Some surfaces SHOULD share source (Sweat Card top_8 reading Sharp Card = correct dependency), others should stay isolated (Ladder is its own model, doesn't touch Sharp Card at all).

**Cross-sport implementation matrix — where each surface stands (as of 9/9):**

| Surface | MLB | NFL | NCAAF | NHL | NBA | NCAAB | UFC |
|---------|-----|-----|-------|-----|-----|-------|-----|
| Sweat Card | ✅ | partial | partial | offseason | offseason | offseason | ⛔ |
| POTD | ✅ | partial | partial | offseason | offseason | offseason | ⛔ |
| Sharp Card | ✅ | ✅ | ✅ | offseason | offseason | offseason | ✅ |
| Ladder | ✅ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| Dawg | ✅ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| Daily Degen | ✅ multi | multi | multi | — | — | — | — |
| Jerry Reads | ✅ full | ✅ per §6.2 | ✅ per §6.2 | wired (Oct 8) | wired (Oct 21) | wired (Nov 3) | own scorer |

**Should some be different (user question):**
Yes — Ladder is a fundamentally different product concept (one play, rolling stake). Should NEVER share selection logic with Sharp Card. Dawg is contrarian-only. Sharp Card is high-confidence board. POTD is highest-single. Daily Degen is high-variance lottery. Jerry Reads is per-game commentary layer. Every one has a distinct use case and should have distinct selection heuristics — but grading + snapshot infrastructure can and should be shared.

**Preferred walkthrough format when we discuss:**
Long-form artifact page with a section per surface, diagram of the selection flow, table of gate rules, and a red-flag box highlighting known drift/failure points. Similar treatment to the tech reference §6 pick generation section.

**Related memory:**
[[project_technical_reference_manual_906]], [[feedback_sweat_card_vs_sharp_card]], [[feedback_sharp_card_composite_record]], [[feedback_daily_yesterday_recap]], [[project_ladder_teasers_817]], [[project_jerry_vs_sharp_card_817]]
