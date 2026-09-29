---
name: Default to universal fixes; only sport-branch when the data model requires it
description: When changing prompts, scoring logic, or entity handling, the default is to apply changes across all sports unless there's a sport-specific reason not to
type: feedback
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
When making changes to prompts, scoring, or any logic that applies to "games" or "plays" or "props", default to **universal fixes** that apply across all sports. Only sport-branch when the underlying data model genuinely differs (e.g., MLB has pitcher xERA, NBA has injuries, UFC has finishing rate — these are sport-specific signals that legitimately need branching).

**Why:** Andy flagged this 2026-04-22. The fear is introducing NFL (or any new sport) once launched and having to rediscover bugs that were "fixed for MLB only" somewhere deep in the prompts/logic. Patches should compose cleanly when new sports come online, not force hunting through sport-specific branches that silently break NFL reads.

**How to apply:**
- **Prompt rules**: if a rule is about TONE (no preamble, no tout language, never refuse a lean, entity isolation, no-repeat closing phrases) — put it in the universal block. If it's about SIGNAL PRIORITY (xERA gap, K rate matchup, OAA, finishing rate) — put it in the sport-specific block.
- **Scoring/Sweat Score**: if the signal is sport-agnostic (injury OUT overrides, rest disadvantage, streak regression) — universal weighting. If it requires sport-specific data fields (MLB xERA, NBA net rating) — sport branch.
- **Entity isolation bugs** (like the Prop Jerry Heineman/Laureano issue where Jerry confused a teammate for a pitcher): the fix should be structural — pass labeled entity fields (subject, subject_team, opponent_team, opposing_pitcher_or_player) so the LLM can't confuse them. That applies to every sport's prop write-ups, not just MLB.
- **When in doubt, ask first**: "Should this be sport-specific or universal?" Getting this right on small decisions prevents a lot of cleanup later.

**Anti-pattern to avoid:** Sprinkling `if sport === 'MLB'` conditionals throughout the prompt/scoring code every time a bug gets fixed. That pattern means every new sport starts with a dozen latent bugs that only surface when someone opens an NFL game card.
