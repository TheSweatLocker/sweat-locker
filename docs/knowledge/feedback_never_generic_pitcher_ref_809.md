---
name: never-generic-pitcher-ref-809
description: "Never ship 'the opposing starter' in Jerry prose. Layer D mechanical scrub substitutes real pitcher name based on context proximity."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T20:06:05.556Z
---

**Rule**: Jerry reads must never contain "the opposing starter" (or "the home/away starter" without a parenthetical name) in shipped prose. Substitute with real pitcher last-name.

**Why**: User caught this pattern 2026-08-09 with 8/15 games leaking the phrase into user-facing reads. Two of them had catastrophic hallucinations where umpire + park factor were labeled AS the opposing starter ("The umpire, the opposing starter, runs a neutral zone"). Reader-facing brand damage — bettors see it as sloppy AI output.

**How to apply**:
- `validate_jerry_read.substitute_generic_pitcher_refs()` is the deterministic Layer D scrub — runs unconditionally after retry regardless of validator verdict
- Wired into `generate_jerry_synthesis.py` after LAYER C (name substitution) + style hard-cap
- Also strips parenthetical/comma hallucinations where umpire/park lines got tagged with "the opposing starter"
- Handles the "Jerry hallucinated lead pitcher name" case by favoring the pitcher named later in the prose
- Test coverage in the module — 7 real leak patterns from 8/9 all resolve CLEAN

**Related failure mode**: When Jerry hallucinates a lead pitcher name (e.g. "Sheehan" for a Wrobleski/Rodriguez game), Layer D can misattribute. Refined to detect this — if lead name isn't in whitelist, prefer the tail-mentioned real pitcher.

Related: [[feedback_verify_pitcher_attribution]], [[project_jerry_attribution_validator]].
