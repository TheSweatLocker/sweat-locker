---
name: feedback_suppression_gate_needs_shadow
description: "A tier/pick suppression gate MUST shadow-record the suppressed decision, or its own exit condition becomes unreachable"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T00:31:53.190Z
---

Any gate that suppresses a tier before write (PRIME→STRONG cap, FADE
demote, family ban) MUST stash the suppressed decision in shadow
(e.g. `signals._shadow_tier`) so it still grades.

Otherwise the gate destroys the evidence its own exit condition requires.
NFL prop PRIME cap (installed 2026-09-14, found 2026-09-20): exit
condition was "n>=100 graded PRIME to validate". The cap rewrote PRIME to
STRONG *before* the row was written, so no PRIME row ever stored, so none
ever graded. Frozen at n=57 forever while a hardcoded expiry date
(2026-09-23) quietly ran out — which would have released 75 PRIME onto one
slate with zero validating evidence.

**Why:** a suppressed pick is still a prediction the engine made. Not
recording it means we can never learn whether the suppression was right,
and "we have no record" is the exact thing Andy fears being called out on.

**How to apply:**
- Cap/ban gates write the uncapped call to a shadow key; never render it.
- Exit conditions are EVIDENCE thresholds (n>=X graded), never calendar
  dates. A date expires whether or not the thing was fixed.
- The evidence query must FAIL CLOSED — return an explicit ok flag and
  keep the gate ON when it's False. PostgREST answers a bad column with
  400 and a dropped pooler with nothing; reading either empty result as
  "n=0, no evidence needed" is how a failed query becomes a published
  slate. See [[feedback_fix_at_root_three_parts]].
- Never gate on a model with negative lift. The NFL prop LR
  (test_acc 49.2% vs baseline 52.3%, lift -3.0pp, 78% of training rows
  from one date) would have been noise dressed as rigor.

Same shape as [[feedback_source_gate_pattern]] and the M1 fix where a
discipline cap was silently undone by a later LR override — a gate a
later stage can erase is not a gate.
