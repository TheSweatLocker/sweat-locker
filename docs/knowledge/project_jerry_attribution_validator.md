---
name: jerry-attribution-validator
description: "Post-LLM validator shipped 2026-06-06 (94d444a) in generate_mlb_game_reads.py — catches the recurring pitcher/hitter team-attribution hallucinations the prompt rules alone couldn't prevent"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

`generate_mlb_game_reads.py` now runs `_detect_attribution_errors(narrative, struct)` after every Claude call. If it flags a pattern, the generator retries Claude with a `_correction_prompt(...)` up to 2x; if still broken, narrative is discarded and only the struct is stored. Better empty than wrong.

**Patterns detected (all conservative — no false positives on the 15 currently-cached 6/6 reads):**
1. `<pitcher_last> <facing-verb> <own_team_keyword>` — e.g. "Vazquez facing the Padres"
2. `<own_team>('s)? ... <plural_noun: lineup|bats|offense|bullpen|...> ... <pitcher_last> <action-verb>` — e.g. "Boston's 90 wRC+ lineup Tolle will punish" (the plural-noun anchor is what distinguishes this from clean possessives like "Cubs' Horton owns the Cards")
3. `<opp_team>('s)? <hitter|batter|slugger|bat|lineup> ... <hitter_last>` — e.g. "Nationals hitters Benge and Soto"
4. `<hitter_last> (<opp_team>)` — parenthetical wrong-team tag

**Why:** Prompt rules (ATTRIBUTION SAFETY, HITTER ATTRIBUTION) keep getting added each time this fires and the LLM still occasionally hallucinates. Server-side post-validation is the only way to make this class of bug not recur — it doesn't depend on the prompt being followed.

**How to apply:**
- When a Vazquez/Tolle/Benge-class report comes in (wrong team attribution in a Jerry read), first check the validator logs for the day — there should be ⚠️/⛔ lines if it caught + retried/discarded.
- If the bug surfaces without the validator catching it, add a new test case to `_test_attribution_validator.py` and tighten the regex. Tests must stay 6/6+ green AND the smoke-test loop over today's cache must show 0 false positives.
- The smoke test: `python -c "from generate_mlb_game_reads import _detect_attribution_errors; ..."` over all `game_read_*` rows in `jerry_cache` for today.

**`_team_keywords()` rules:**
- "San Diego Padres" → `["Padres", "San Diego"]`
- "Boston Red Sox" → `["Red Sox", "Sox", "Boston"]`
- "Chicago Cubs" → `["Cubs"]` (Chicago dropped — ambiguous with White Sox)
- "New York Mets" / "Los Angeles Dodgers" → nickname only (cities are ambiguous)
- "Athletics" → `["Athletics"]` (no city since 2026)

Linked: [[feedback_validate_data_reaches_new_code]] (this is post-validation as the canonical pattern), [[project_jerry_opp_lineup_attribution]] (the 5/16 first attempt to fix this via prompt — left the failure mode partially open), [[project_v4_blackout_606]] (today's morning audit that surfaced this alongside 4 other silent-failure classes).
