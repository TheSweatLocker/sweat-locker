---
name: project-next-session-queue-817
description: 2026-08-17 END-OF-DAY queue for next session. Priorities from user pre-bed handoff.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-18T03:16:45.537Z
---

User handoff before bed 2026-08-17. Attack in this order:

## 1. 🚨 Sharp Card date bug (Yankees/Orioles leak)

**Confirmed bug:** Sharp Card showing 8/18 game (NYY @ BAL Yankees ML)
on 8/17. Root cause: fetcher uses `new Date().toISOString().slice(0,10)`
which is UTC. After ~8pm ET, UTC has rolled to tomorrow → wrong "today".

**Fix**: In `app/index.tsx` `fetchSharpTab()` (line ~8448), change:
```js
const today = new Date().toISOString().slice(0,10);
```
to:
```js
const today = new Date().toLocaleDateString('en-CA', {timeZone: 'America/New_York'});
```

Same pattern probably affects Ledger fetcher (`fetchLedger`) and
ladder fetcher — audit all Steam Room tab fetchers for the same bug.

## 2. 🪜 Ladder cadence — 3 days silent is unacceptable

User: "no ladder in three days is unsat, need to happen at least every
other day, maybe make props part of it if not already, at the very
least wire all playbooks to it in some form."

Look at `mlb_pipeline/steam_room_ladder.py`. Investigate:
- Why silent 3 days? Check qualifier gates + workflow logs
- Loosen qualifier if too strict (target: 1 pick per ~2 days min)
- Wire props as candidate leg source (currently sides-only?)
- Cross-sport: does Ladder pull from all sport playbooks or MLB only?

## 3. 🌊 Line Movement tab — KEEP, refine

User reversed prior "kill it" call: "signals looked like they made
sense today. Strongest signals all did solid. Let's just take a look
see how we can make it a little better." DO NOT deprecate.

Look at what's rendering + what signals actually fired today. See if
we can add: sharp/public split delta prominence, RLM visualization,
per-book divergence highlights.

## 4. 🔒 Hardcoded audit

User: "Look at what is all hardcoded and what reads off backend. Make
sure all things hardcoded are things that will never change to prevent
submissions once online."

Audit `app/index.tsx` for:
- Tier thresholds hardcoded vs backend-driven
- Sport lists / labels
- Copy strings
- Odds format assumptions
- Prop_type mappings
- Book names / logos

Anything that COULD change (thresholds, cohort labels, tier weights)
should be backend-driven so we don't need App Store resubmission.
Truly immutable stuff (sport names, "Steam Room", "Sharp"/"Ladder"
labels themselves) OK to hardcode.

## 5. ✨ "The" prefix on Steam Room subtabs

User: "Add The to all subtabs — The Ledger, The Sharp, etc."

Current app labels (from earlier work):
- "🌊 Lines" → "🌊 The Lines" (or keep short?)
- "🪜 Ladder" → "🪜 The Ladder"
- "🎯 Sharp" → "🎯 The Sharp"
- "🧾 Ledger" → "🧾 The Ledger"

Verify with user which get "The" — probably Ledger/Sharp/Ladder yes,
Lines maybe stays "Lines" for brevity.

## 6. 🔍 MLB grader tomorrow morning

Grader hasn't run for 8/17 yet — all PRIME/STRONG props still pending.
Tomorrow AM check results, particularly:
- Playbook overrides of refit trap fades (Imanaga/Sugano BB U)
- Batter hits O 0.5 stack (7 STRONG picks, refit skeptical)
- Outs U (Barnett + Mlodzinski — refit 100 + L10 extreme)

If refit was right (traps hit unders anyway), playbook is over-weighting
form. If playbook was right (unders held), multi-signal reasoning
validated.

## 7. 💡 Model brainstorm — save for discussion

User: "Need to brainstorm additional models for NBA/NHL, how do we get
there?"

Ideas parked for discussion:
- NBA #3: four-factor (Basketball Reference scrape)
- NBA #4: Vegas MC simulator (10k possession-based sims)
- NBA #5: public/sharp splits (extend fadereport)
- NHL #2: NHL Stats API L10 rolling (build own from game logs)
- NHL #3: powerplay differential
- NHL #4: Naturalstattrick advanced 5v5 scrape

Every model = signal_sources rows. No ensemble scorer change needed.

## 8. Migration pending

`20260817_nba_projections.sql` — applied per user. All 8+ migrations
from 8/17 session applied except possibly stragglers.

Related: [[project-elo-models-shipped-817]],
[[project-nhl-rebuild-status-817]], [[project-nba-rebuild-status-817]]
