# Splitting `mlb_pipeline.yml` without a flag day

**Written for: Andy.** Answering "what's the path forward to optimizing the
mlb pipeline split without breaking shit?"

## What we're actually splitting

`mlb_pipeline.yml` is **178 steps**, classified by the sport scope of the
scripts each one runs:

| | steps | |
|---|---|---|
| look MLB-only | **126** | pitcher stats, Savant, props, NRFI, HR Watch, umpires |
| **shared / cross-sport** | **42** | Sweat Card, POTD, Sharp Card, Steam Room, Ledger, splits scrapers, surface records, cohorts, external sources, watchdogs |
| setup / heartbeat / commit | 9 | checkout, pip, pings |
| explicitly another sport | 1 | "Pull NFL team stats" → runs `nfl_pipeline.py` |

So the split is: **42 + 1 steps move out, 126 stay.** That is the whole job,
and it is why the Sweat Card currently composes on yesterday's non-MLB data —
the card is built inside the MLB pipeline, so it necessarily runs before the
NBA/NHL/NCAAB pipelines that are landing 9h27–9h35 late.

## The four risks, named

1. **Internal ordering inside the 42.** They are not independent — "Re-walk
   Sweat Card (post-POTD)" has to follow "Generate Play of the Day", which
   has to follow "Jerry-anchor tonight's POTD". Any extraction must preserve
   the existing sequence *exactly*, not a reconstruction of it.
2. **Cross-boundary dependencies.** Several of the 42 read MLB outputs
   (`generate_sweat_card.py` reads `mlb_game_context`). Moving them later is
   safe; moving them *earlier* would break them.
3. **Mis-scoped names, in both directions.** "Monitor ensemble health (MLB)"
   and "Externals watchdog (MLB)" are MLB-scoped despite shared-looking
   filenames; "Enrich team form — venue split + H2H (universal)" is
   cross-sport despite sitting in the MLB file. **The step name is not the
   scope** — 2026-11-03 nearly gated the Sweat Card dark on exactly this.
   Every one of the 43 needs its arguments read, not its title.
4. **A flag day.** A single commit that moves 43 steps and rewires triggers
   has no safe rollback once the night's run is half done.

## The path: four stages, each independently revertible

### Stage 1 — extract, call in place (no behaviour change)

Move the 42+1 shared steps into `.github/workflows/shared_surfaces.yml` with
`on: workflow_call`, and have `mlb_pipeline.yml` invoke it as a reusable
workflow **at the same position in the sequence**.

- Behaviour is byte-identical: same steps, same order, same runner.
- **Verification is mechanical**: dump the ordered step list before and after
  and diff it. It must be identical modulo the job boundary.
- Rollback: revert one commit.

### Stage 2 — add the second caller, in shadow

Give `shared_surfaces.yml` a `workflow_run` trigger on NBA / NHL / NCAAB /
Nightly Cross-Sport completion (`daily_card.yml` already has this pattern
from 2026-11-03), but have it run **read-only / dry-run** where each script
supports it, and compare what it would write against what the in-place call
produced.

- Nothing user-facing changes. We find out whether running *after* the sport
  pipelines actually yields a fuller card before we depend on it.
- This is the stage that answers the real question: is the card thin because
  of ordering, or because the engine has no opinion on those sports? We
  already know NBA is 86% COVERAGE and NHL 92%, so **expect ordering to be
  necessary but not sufficient.**

### Stage 3 — flip ownership

Remove the in-place call from `mlb_pipeline.yml`; `shared_surfaces.yml` owns
the block, triggered after the last sport pipeline.

- Needs a `concurrency` group so two sport pipelines finishing together do not
  run the shared block twice (`cancel-in-progress: false`).
- Needs a floor: if no sport pipeline completes by a deadline, run anyway, or
  the card never builds. A cron backstop at a late minute covers that.

### Stage 4 — gate MLB for the offseason

With `mlb_pipeline.yml` down to 126 MLB-only steps, the `sport_gate.py` check
already shipped tonight applies to it cleanly — which it cannot today, because
gating it would take the card with it.

## Sequencing note

Stage 3's benefit is bounded by the lateness it is meant to work around.
Tonight's minute-spread took :00 from 56.7% of firings to 21.2%; if the sport
pipelines come back on time, Stage 3 is a real fix. If they are still hours
late, the shared block will simply wait for them and the card gets later
rather than fuller. **Measure landing times for a few days after the spread
before committing to Stage 3.**

## What I would not do

- Don't move steps by name. Read each one's arguments.
- Don't reorder while extracting. Extract first, reorder later if ever.
- Don't start at Stage 3 because it is the one with the payoff.
