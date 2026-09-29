---
name: feedback-brand-attribution-803
description: Jerry synthesis prose never names external data providers or handicappers; use generic language
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-03T15:36:57.520Z
---

Jerry's SHORT and LONG synthesis prose NEVER names specific data providers or
individual handicappers. Refer to sources generically.

**Why**: 2026-08-03 audit found 49 of 63 recent MLB Jerry reads (78%) cited
"OddsCrowd" by name. Also caught: Action Network, Doc Sports, Betfirm, Rocky
Atkinson, VSiN Peterson, Circa. Same principle as
[[feedback_no_kenpom_attribution]] — OddsCrowd + others escaped that earlier
rule. Real problems:
  1. Users think we're aggregating a competitor's proprietary product
  2. Legal/ToS risk — providers can object and force yanking historical reads
  3. Weakens brand — makes Jerry sound like an aggregator not our own analyst
  4. Drives users to competitor sites

**Approved generic phrasings** (Jerry may use these):
  - "sharp money data" / "sharp action"
  - "public bet%" / "public money%"
  - "external handicappers" / "external consensus"
  - "3 of 5 external handicappers agree"
  - "line movement" / "line moved to X"
  - "market money flow"
  - "aggregated picks"

**Banned in Jerry prose** (never write these in synthesis output):
  OddsCrowd, Action Network, VSiN, Circa, Pinnacle, Covers, Doc Sports,
  Betfirm, Rocky Atkinson, Tony's Picks, Rotogrinders, KenPom, FanGraphs,
  Statcast (as attribution), DraftKings, FanDuel, BetMGM, Hard Rock,
  ESPN BET, and any other specific brand/handicapper by name.

**The distinction that matters** (BOTH allowed by design):
  - **Jerry's prose**: fully generic — "external handicapper consensus"
  - **External Picks panel in app**: shows raw picker names + track records
    (proper attribution when displaying a picker's own pick). That's separate
    UI from Jerry's synthesis and stays branded.

**How to apply**:
  - Every new sport's Jerry prompt template MUST include the BRAND ATTRIBUTION
    GUARDRAIL block (see MLB jerry_synthesis template as canonical)
  - Post-LLM validator (Sprint 2 work) should scan Jerry output for banned
    brand names and regenerate if any surface
  - When adding a new data source, check whether it needs to be added to
    the banned list

Historical MLB reads (pre-2026-08-03) still contain brand mentions. Bulk
find/replace pass to sanitize is a nice-to-have but not shipped yet.

Related: [[feedback_no_kenpom_attribution]] (parent rule for KenPom
specifically), [[feedback_verify_pitcher_attribution]] (similar guardrail
approach for hallucination prevention).
