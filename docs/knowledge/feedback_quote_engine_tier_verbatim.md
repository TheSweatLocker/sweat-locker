---
name: feedback_quote_engine_tier_verbatim
description: HARD rule for any public-facing pick writeup. Pull the live resolver tier from the game_read jerry_cache row and quote IT — never paraphrase from cohort_signals or invent conviction language louder than the engine's own label. Recurring failure pattern user has flagged multiple times.
metadata:
  type: feedback
---

**HARD RULE — non-negotiable, applies before EVERY public-facing pick writeup, slide, social caption, or card content:**

1. **Pull the live engine read first.** Query `jerry_cache` for `game_read_<game_id>_<date>` and extract:
   - `resolver_side.tier` (ELITE / STRONG / LEAN / LIGHT / SKIP)
   - `resolver_side.direction` and `resolver_side.team`
   - `resolver.tier` (same scale, for totals)
   - `resolver.direction` for totals
   These are the engine's authoritative reads.

2. **Quote that tier verbatim in the writeup.** Use the EXACT word from the resolver:
   - "STRONG side signal" — only if `resolver_side.tier == 'STRONG'`
   - "LIGHT cohort lean" — when `resolver_side.tier == 'LIGHT'`
   - "ELITE total play" — only if `resolver.tier == 'ELITE'`
   - DO NOT write "STRONG" when the resolver said LIGHT
   - DO NOT write "loudest signal" / "highest conviction" / "strongest read" without verifying that's literally what the engine said

3. **Cohort signals are SUPPORTING evidence, not tier source.** A cohort row showing "+10.8 dev" or "82% historical" is a single input to the resolver. The resolver synthesizes all inputs and outputs a tier. The TIER is the citation — the dev/% are supplementary context.

4. **If the engine said LIGHT but my narrative wants to sell STRONG, downgrade the writeup.** Match the engine. Never the other way.

5. **If unsure, pull the actual game_read row before writing.** Five extra seconds beats a misread public post.

**Why this is non-negotiable:**

User quote (2026-06-18 night):
> "Your misread of data over and over again on public picks we post for content is unsat and it keeps happening. The content we post directly reflect what kind of traction the app will get and if you keep feeding me bullshit made up things or overselling this app will not even get off the ground, shit picks equals loss of following."

This is launch-critical credibility. Overselling a LIGHT pick as STRONG and losing it publicly trains followers that our STRONG label means nothing. The brand value of tiered conviction collapses the first time a "STRONG" pick is actually engine-LIGHT and loses.

**Recurring failure pattern this fixes:**

- 6/18: NYY ML writeup said "STRONG side resolver +10.8 dev" — engine read shows LIGHT
- 6/17 AM: Multiple POTD swap attempts based on my interpretation vs engine's call (engine was right, I was wrong)
- 6/15-16: Cease team confusion (saved as feedback_verify_player_team_first — same family of errors)

**Mechanical implementation when assembling content:**

```python
# BEFORE writing any pick writeup:
r = requests.get(f'{SU}/rest/v1/jerry_cache',
    params={'cache_key': f'eq.game_read_{game_id}_{date}', 'select': 'data'},
    headers=H)
read = r.json()[0]['data']
side = read.get('resolver_side') or {}
tot = read.get('resolver') or {}

engine_side_tier = side.get('tier')  # AUTHORITATIVE — quote this
engine_side_direction = side.get('direction')
engine_side_team = side.get('team')
engine_total_tier = tot.get('tier')
engine_total_direction = tot.get('direction')

# Writeup language MUST match:
if engine_side_tier == 'STRONG':
    # OK to say "STRONG side resolver" / "model majority + cohort aligned"
elif engine_side_tier == 'LIGHT':
    # Say "LIGHT cohort lean" / "cohort engine alone" — NOT "STRONG" or "loudest"
elif engine_side_tier == 'SKIP':
    # Don't surface as a side pick at all
```

**When to flag to user:**

If the engine's tier is LOWER than the conviction I'd want to write at, FLAG IT rather than oversell. Example: "The engine reads NYY ML as LIGHT today, not STRONG. The cohort engine alone is supporting the call, the models aren't agreeing. I'd downgrade this from public-card placement."

Honest read > forced confidence.

Related: [[feedback_verify_player_team_first]] [[feedback_user_doubt_is_signal]] [[feedback_confidence_in_first_pass]]
