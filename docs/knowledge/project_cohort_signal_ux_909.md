---
name: project-cohort-signal-ux-909
description: "9/9 design queue — Cohort Signal section on game card leaks internal keys (HFA, cope, def_splash). Translate to plain English + attach per-cohort record + surface net directional read."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-09T16:45:18.896Z
---

🎯 9/9 UX debt. Game card "Cohort Signal" section currently renders raw
cohort keys with directions ("HFA - Home, cope Away, def_splash Home").
User asked: what is the user getting out of that? Answer today: nothing.
Same disease as the badge chip pre-record (see [[project-badge-record-ledger-909]]) —
label with no credential + jargon a bettor can't parse.

**Why:** Cohorts are the [[project-cohort-engine-universal-architecture]] MOAT — 
proprietary groupings that historically lean a direction. The section
proves the MOAT exists, but internal names make it read as noise instead
of insight. Aligns with [[project-casual-bettor-ux-docket]] "translation
not simplification" theme and [[feedback-sample-size-with-pct]] (every %
shows n).

**How to apply:** Every cohort key needs (1) a plain-english display
name, (2) a one-line explainer, (3) its historical record on similar
games, (4) roll-up into a single net read.

## What the user gets today

```
COHORT SIGNAL
HFA — Home
cope — Away
def_splash — Home
```

User reaction: "what is cope? what is def_splash? why are these here?"

## What the user should get

```
COHORT READ  ·  Net: HOME +1.8u
  🏟  Home-Field Edge         Home  ·  58-42 ATS (58%, n=100)
  🔁  Regression to Mean      Away  ·  42-38 ATS (52%, n=80)
  🛡  Elite Defense Spot      Home  ·  33-20 ATS (62%, n=53)
```

Three deltas from today:
1. Plain-english label (Home-Field Edge, not HFA)
2. Cohort's historical hit rate on similar games (record + n + %)
3. Net roll-up header so scanners get the punchline without reading each row

## Translation layer

Map internal keys → display names. Table `cohort_display_registry`:

```
cohort_key            display_name              short_explainer                                        emoji
hfa                   Home-Field Edge           Home teams historically outperform market in this spot 🏟
cope                  Regression to Mean        Team due for correction after outlier stretch          🔁
def_splash            Elite Defense Spot        Defense mismatch favors this side                      🛡
rest_edge             Rest Advantage            Bigger rest gap favors this team                       😴
line_slam             Sharp Line Move           Line moved through key number against public          💰
public_fade           Contrarian Spot           Heavy public one way, sharps other                     🎯
divisional            Divisional Familiarity    Div matchups play tighter than market prices          ⚔️
regress_pace          Pace Correction           Pace mismatch favors slow team                        🐢
```

Registry is versioned, sport-scoped where meanings diverge. Cohort key
without a display entry silent-hides on card (never render "cope" to a user).

## Record attachment

Same pattern as [[project-badge-record-ledger-909]]. New table
`sport_cohort_records`:

```
sport            text
cohort_key       text
window           text        -- 'l2yr', 'ytd', 'lifetime'
wins             int
losses           int
pushes           int
units_won        numeric
sample_n         int
avg_edge_pct     numeric      -- how much % above 50 baseline
updated_at       timestamptz
PRIMARY KEY (sport, cohort_key, window)
```

Backfill by iterating graded games and evaluating each cohort against
closing context — same rule functions the live cohort engine uses.
Hide record line when n < 30.

## Net roll-up

Sum per-side units_won across all cohorts applicable to today's game.
Show as header pill:

- Net favors Home: "COHORT READ · HOME +2.1u"
- Net favors Away: "COHORT READ · AWAY +0.8u"
- Cancel out: "COHORT READ · SPLIT" (hide the section entirely — no
  edge to surface)

This gives scanners the answer in one line and turns the detail rows
into supporting evidence instead of a wall of jargon.

## Suppression rules

- Hide any cohort with lifetime win% below 52% (not enough edge to
  justify the pixel real estate)
- Hide entire section when < 2 cohorts qualify (isolated cohort is
  noise, needs corroboration)
- Cap at 3 cohorts shown per card (top by |units_won|)
- Never show a losing cohort — if a cohort historically loses on
  this pattern, flip its direction ("Fade Regression" instead of
  showing regression favoring wrong side)

## Cross-sport

Cohort engine already runs cross-sport. Registry needs sport-scoped
entries where a key means different things — e.g., "pace_gap" in NBA
vs NCAAB have different thresholds. Default to sport-agnostic entry
if sport-specific missing.

## Queue position

Ship as bundle with [[project-badge-record-ledger-909]] — same
plumbing (records table + backfill + registry lookup + card render).
One backend session (schema + backfill + display registry seed) + one
frontend session (card render for both badge records and cohort read).

Not launch-blocking. Post v1.0.1.
