---
name: dont-fade-prime-on-pattern-alone
description: When pipeline surfaces PRIME with pipeline reason (mastery, vs_team_era, cohort), don't override with recency pattern-reading. 7/19 both Pérez PRIME 86 (I faded, won) and WSH ML PRIME 62 (I faded, won).
metadata:
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Set 2026-07-19 after two fade recommendations were wrong on same slate.**

## The rule

If pipeline surfaces PRIME with a clear data reason (vs_team_era mastery, cohort net LOUD, primary_play flag with audit_note >= 50%), do NOT recommend a fade based on:
- Yesterday's game pattern ("chalk trap")
- Season xERA when vs_team_era conflicts
- Public bet% (public can be right on obvious plays)

## Why

7/19 both my key fades were wrong:

**1. Eury Pérez HA U 4.5 PRIME 86**
- I said: "xERA 5.82 is wrong profile, mastery-vs-team doing all the projection heavy lifting"
- Reality: Pérez went 6 IP · 1 H · 9 K. Mastery flag (vs_team_era 2.30) was RIGHT.
- Pipeline had this as pipeline-STRONG-Under confidence. My "gut" override was garbage.

**2. WSH ML PRIME 62 primary_play**
- I said: "Same 84% public trap as yesterday's 15-1 loss. Fade."
- Reality: WSH won 5-2. Primary_play flag was correct.
- The 52.5% audit note is real but that's still +EV at plus money (+123).
- I overweighted one-day recency (yesterday's 15-1 loss).

## How to apply

Before recommending a fade of a PRIME/STRONG pipeline surface, ask:
1. Is my counter-reason a specific data conflict (e.g., pitcher just hurt, lineup changed) or is it *pattern-reading*?
2. What is the pipeline's own signal source? If it names a specific edge (mastery, cohort, confluence), trust it unless I have a specific contradicting stat.
3. One-day patterns are noise. 30d patterns are signal. Distinguish.

If the fade is only "yesterday burned us on similar setup" — that's not a valid fade reason on a PRIME surface. Post the pipeline play, not the counter-narrative.

## Exceptions

- Card display bug (line mismatch, wrong player) — always fade
- Juice priced above breakeven (per [[feedback_card_process_discipline_718]] rule 6)
- Pipeline projection uses inputs known-stale (lineup unconfirmed for props that depend on it)

## Related

- [[feedback_card_process_discipline_718]] — juice check, discipline caps
- [[feedback_let_engine_speak]] — trust net engine count over narrative
- [[project_30d_lens_audit_718]] — pattern-reading pitfalls
