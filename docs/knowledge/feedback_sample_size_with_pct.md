---
name: feedback_sample_size_with_pct
description: Every percentage cited on a user-facing surface (Jerry reads, sweat card drivers, Why This Score, Numbers panel, slides, social) must show sample size (n) alongside. No naked percentages. Both numbers must be live, not hardcoded.
metadata:
  type: feedback
---

**Rule:** Every percentage on a user-facing surface must:

1. **Show n alongside** — "67% hit rate (n=58)" not "67% historical hit rate"
2. **Be live + variable** — both the % and the n update together from the same source
3. **Be gated by n** — when n < quotability threshold (we use n ≥ 30), the % isn't cited at all. Cohort label can still surface but the rate is omitted.

**Why:** Without n, "67% historical" could mean 67% of 3 games (coin flip noise) or 67% of 600 games (genuine cohort signal). Hidden denominators erode trust and let weak samples masquerade as edge.

**How to apply:**
- Writing Jerry narrative → never quote a % without the n right next to it
- Writing social slides / captions → same rule
- Writing app driver labels → include n in the data structure so UI can render
- Code that generates % strings → must pull both numerator and n from the live struct
- LLM validators (Phase 4 number-attribution) → flag % without proximate n citation OR high-n struct backing (extended 2026-06-18)
- Threshold for "quotable without inline n": n ≥ 30 (matches buy-down calibration MIN_N_TO_QUOTE)

**Good examples:**
- `Cohort hits 67% (n=58, 39-19 lifetime)`
- `Model edge 80% across 42 games`
- `STRONG_EDGE tier (70.4%, 28-11 over 39 games)`

**Bad examples (would be flagged):**
- `Cohort 67% historical hit rate`
- `80% backtest hit rate`
- `Strong cohort with 76% conviction`

Related: [[project_unified_taxonomy_decision]] [[reference_data_source_strategy]]
