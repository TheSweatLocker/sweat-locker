---
name: project_nhl_read_card_drift_929
description: NHL has three win probabilities; the read quoted the invisible shadow one and argued against its own pick. Not a timing drift.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-29T12:46:00.625Z
---

**This file first claimed the NHL reads had drifted because they were generated
2.5h before the context rebuild. That was WRONG and is corrected below.**
`jerry_reads.input_snapshot` for MTL@TOR holds `projected_home_wp` 0.559 and
`mc_p_home` 0.4701 — identical to current `nhl_game_context`. The 23:55
`updated_at` bump was the splits aggregator writing `splits_summary`, not the
projections. Workflow order was already correct (ctx step 146, reads step 240).

**The real defect (measured 2026-09-29, fixed in 83f5d4e8).** An NHL game
carries THREE win probabilities and the card showed neither of the two that
matter:

| field | value (MTL@TOR) | status |
|---|---|---|
| `projected_home_wp` | 0.559 | Elo. **What the pick is built on** ("56% vs 53% implied, +2.5pp"). Selected by index.tsx, rendered NOWHERE. |
| `primary_play._lr_ml_shadow.p_home_win` | 0.482 | Shadow, `suggested_side: NONE`. LR tile exists only in NCAAF/NFL branches; LR chip suppresses 0.45-0.55 as PASS → invisible on NHL. |
| `mc_probabilities.mc_p_home` | 0.470 | Monte Carlo. The only one on screen. |

Result: the read said *"The model has Toronto at 48.2% ... stripping away any
edge on the moneyline"* attached to a **Toronto ML pick, conviction 56**. A read
arguing against its own call, citing a figure absent from the card.

Elo and MC disagree on the **SIDE** on multiple games, not just in magnitude:
MTL@TOR (Elo H56 / MC A53), NYI@TOR (H60 / A51), PHI@NJ (H60 / A51).

**How to apply:**
- `primary_play` is dumped whole into read prompts. Any `_`-prefixed field or
  `audit_note` becomes quotable "fact" to the writer. Sanitize at the source —
  a prompt rule is not a control. See `_public_primary_play`.
- When adding a lens, check it is actually RENDERED for that sport. Three
  separate NHL surfaces (LR tile, LR chip, Elo) were fetched-but-invisible.
- Never add a second key holding the same number to "clarify" — that is how
  one-model-shown-twice starts ([[project_ncaaf_duplicate_total_lens_920]],
  and NHL's MC simulating v3's own lambdas).

**Also found 2026-09-29:** `anthropic_guard.py`, written 9/28 for fatal-vs-
transient LLM errors, was imported by NOTHING. A 401 invalid-key produced six
warnings and **exit 0**. Now wired into generate_nhl/nfl/ncaaf_game_reads and
generate_jerry_synthesis. Also `--force` on generate_nhl_game_reads is a no-op:
`run()` accepts it and never reads it, so every run regenerates every game.

Related: [[project_nhl_buildout_927]], [[project_nhl_projection_calibration_927]],
[[feedback_explicit_select_silent_blanks]].
