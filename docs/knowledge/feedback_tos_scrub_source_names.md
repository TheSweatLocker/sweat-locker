---
name: feedback-tos-scrub-source-names
description: 2026-08-21 policy — abbreviate/genericize external source names in all user-facing surfaces to avoid ToS violations.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-21T23:45:27.431Z
---

**8/21 user directive:** strip actual names of external sources (handicappers, split data providers, MMA analysis sites) from all user-facing app surfaces. Use abbreviated codes like the split-source pattern already does (FR / CZ / OC).

**Why:** displaying full brand names of scraped sources risks ToS violations from those sites and could invite legal action. Same reason we already do this for splits.

**How to apply:**
- Handicapper labels use 2-3 letter codes (AN, DM, CV, VS, PW, PD, BP, DS, CBS, OS, FG, BPP, SCP)
- Split sources continue using FR (FadeReport) / CZ (Cleatz) / OC (OddsCrowd)
- LLM prompts must not instruct AI to name specific external sources — describe "consensus" or "public analyst read" generically
- User-facing legal disclosure says "multiple industry sources — codes shown in-app" rather than enumerating names
- Code comments MAY reference names for developer context (not user-facing = OK)
- Same discipline applies cross-sport for any web-search-driven prose in NFL/NBA/NHL/UFC

**Exceptions (still OK):**
- Brand attribution for OUR OWN data (e.g. "Sweat Locker model") — that's us
- Sportsbook names when referencing where user should verify odds — book names are public

**Scope:** all in-app text and any LLM output that reaches users. Not internal analytics/logs.

Related: [[feedback_brand_attribution_803]] (Jerry prose already scrubs source names — this extends the same discipline to External Picks panel + legal + UFC prompts), [[feedback_no_kenpom_attribution]].
