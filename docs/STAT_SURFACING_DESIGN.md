# Stat surfacing — matchup pairs, all sports

**2026-10-14.** Andy: *"what is th so what, do we expect every user to know how
to put 22 and 2 together from the stats... should we discuss changing how it's
laid out like we discussed in football where user can see offensive rushing
yards and defensive rushing yards allowed side by side to see more matchup, but
do that equivalently to NHL... all sports need to be better for stat reading, idk
how we get there."*

This is the design, grounded in columns that are **measured as populated today**
rather than columns that exist.

---

## 1. The problem, stated precisely

We ship **levels** and outsource the reasoning. A user sees "ATL rush 166.8" on
one row and "CHI rush defence 93.3" somewhere else, and has to (a) notice they
belong together, (b) know which direction is good, and (c) know whether 166.8
is a lot. Three jobs, none of them the user's.

That is the "22 and 2" problem. The number is not the unit of meaning — the
**matchup** is.

## 2. The universal pattern: attack vs resistance

The football idea generalises, and this is the key finding: **every sport we run
has an "attack vs the thing that stops it" pair already populated.** It is not
`home_X` vs `away_X` (that is the same stat twice). It is
**`home_<offence>` vs `away_<defence>`** — a cross-pairing.

Measured on upcoming games, 2026-10-14:

| sport | n upcoming | cross-pairs available |
|---|---|---|
| NFL | 193 | rush yds ↔ rush yds allowed · pass yds ↔ pass yds allowed · rush EPA ↔ rush EPA allowed · pass EPA ↔ pass EPA allowed · sacks ↔ sacks suffered |
| NCAAF | 65 | all of the NFL set **plus** explosiveness ↔ explosiveness allowed · success rate ↔ success rate allowed · RZ TD rate ↔ opp RZ TD rate |
| NHL | 46 | **PP% ↔ PK%** · xGF/60 ↔ xGA/60 · high-danger for ↔ high-danger against |
| NBA | 35 | off rating ↔ def rating (both directions) · pace ↔ pace |
| MLB | postseason | team OPS ↔ opposing SP ERA / bullpen ERA · L10 runs/g ↔ L10 runs allowed |
| NCAAB | 0 (opens Nov 3) | inherits the NBA shape |

## 3. THE ANSWER TO "how do we get there": ship the RANK, not another number

A raw number is unreadable. **A rank is instantly readable.** "93.3 rush yards
allowed" means nothing to a user; "**29th of 32**" means everything, and it
needs no explanation, no glossary and no arithmetic.

NFL already proves the pattern works — `sor_rank` and `sos_rank` are populated
193/193. We have the ranking machinery; we just do not use it on the stats
users actually read.

**The single highest-leverage change is not a new layout — it is attaching a
rank or percentile to every number we already show.** The layout below is how
to spend that.

---

## 4. The mock — NFL (real numbers, CHI @ ATL, 2026-10-18)

Verified from `nfl_game_context`: market spread −2.5, total 50.5.

```
┌─ THE MATCHUP ────────────────────────────────────────────────┐
│                                                               │
│  ATL RUN GAME                      CHI RUN DEFENCE            │
│  166.8 yds/g                       93.3 yds/g allowed         │
│  ███████████████████░  4th         ████░░░░░░░░░░░░░  29th    │
│                                                               │
│  Atlanta's 4th-ranked run game meets Chicago's 29th-ranked    │
│  run defence.                                                 │
│  ───────────────────────────────────────────────────────────  │
│  ATL PASS GAME                     CHI PASS DEFENCE           │
│  208.2 yds/g                       237.9 yds/g allowed        │
│  ████████░░░░░░░░░░░  24th         ██████████░░░░░░  19th     │
│                                                               │
│  ───────────────────────────────────────────────────────────  │
│  Market: ATL −2.5, total 50.5 — the line already reflects     │
│  both of these.                                               │
└───────────────────────────────────────────────────────────────┘
```

Three things the layout does: pairs the stats, ranks them, and **puts the market
number inside the same frame** so the rank never reads as a free edge.

## 5. The mock — NHL (real numbers, DAL @ COL, tonight)

Verified from `nhl_game_context`: puck line −1.5, total 6.0, ML 115/−138.

```
┌─ THE MATCHUP ────────────────────────────────────────────────┐
│                                                               │
│  SPECIAL TEAMS — the cleanest edge in hockey                  │
│  COL POWER PLAY  23.8%        vs    DAL PENALTY KILL  85.0%   │
│  ████████████░░░░  9th              ████████████████░  6th    │
│  A top-10 power play into a top-10 kill. Cancels out.         │
│  ───────────────────────────────────────────────────────────  │
│  EXPECTED GOALS                                               │
│  COL CREATES  2.95 xGF/60     vs    DAL ALLOWS  2.12 xGA/60   │
│  ██████████████████░  5th           ████████████████░  7th    │
│  ───────────────────────────────────────────────────────────  │
│  GOALIE  ← the most human-legible stat in hockey, and today    │
│           it is buried                                        │
│  M. Blackwood   .879 SV   GSAA −3.48                          │
│  J. Oettinger   .944 SV   GSAA +6.55   ← 10 goals better      │
│                                                               │
│  ───────────────────────────────────────────────────────────  │
│  Market: COL −138, puck line −1.5, total 6.0                  │
└───────────────────────────────────────────────────────────────┘
```

**The goalie row is the biggest single win available in NHL.** A named starter
with a save percentage is the one hockey stat every user already understands,
and Oettinger at .944 against Blackwood at .879 is the whole story of this
game — currently shown as two numbers in a list.

Lead NHL with **PP% vs PK%**: causal, intuitive, and the numbers are far enough
apart to read at a glance.

## 6. The mock — NBA (real numbers, CHA @ BKN)

```
┌─ THE MATCHUP ────────────────────────────────────────────────┐
│  BKN OFFENCE  105.1 off rtg   vs    CHA DEFENCE  114.2 def rtg│
│  CHA OFFENCE  105.1 off rtg   vs    BKN DEFENCE  112.2 def rtg│
│  PACE  BKN 96.4  ·  CHA 97.6      → ~97 possessions           │
│  INJURY IMPACT  BKN 0 ppg out  ·  CHA 0 ppg out               │
└───────────────────────────────────────────────────────────────┘
```

NBA is the thinnest — 10 populated pairs vs NFL's 40. Pace is the sport's best
"so what" (it converts ratings into an expected total), and `injury_impact` in
points-per-game lost is already in the right unit for a user.

## 7. MLB and NCAAF notes

- **NCAAF is the richest** (62 pairs) and should get the full NFL treatment plus
  **explosiveness ↔ explosiveness allowed**, which has no NFL equivalent and is
  the most predictive college-specific pair we store.
- **MLB's natural pair is not team-vs-team**: it is **lineup vs the starter**
  (team OPS / OPS-vs-hand against the opposing SP's ERA, xERA and K%), then
  **lineup vs the bullpen** for innings 7-9. That is how a baseball fan already
  thinks about a game.

---

## 8. Minimising the "market already priced it" risk

Andy concurred this is the danger. Five concrete guards, in order of strength:

1. **Frame it as explanation, never as a recommendation.** Section header is
   *"The matchup"* or *"Why this game looks the way it does"* — never *"Edge"*,
   *"Advantage"* or *"Value"*. The copy describes; it does not advise.

2. **Put the market number in the same frame.** Every mock above ends with the
   line and total. If the user reads "4th-ranked run game vs 29th-ranked run
   defence" and then "ATL −2.5", they draw the right conclusion unaided: the
   line knows.

3. **Show the RANK, never a computed delta.** A rank is descriptive. The moment
   we render "+73.5 yard advantage" we have manufactured a number that looks
   like an edge and is not one. **Never subtract the pair for the user.**

4. **Say so out loud when a gap is extreme.** On a lopsided pair, one line:
   *"Cancels out"* / *"the market has this at X, which already accounts for
   it."* The DAL/COL special-teams mock does this.

5. **Where we genuinely want to assert value, show the RESIDUAL, not the
   level.** The only quantity the market has not already priced is how much a
   team's games have *beaten the number* — and we now store it:
   `total_resid_mean`, `total_resid_l5`, `resid_mean`, `resid_l5` in
   `feature_history`, across all six sports. That is the honest "so what".

**The evidence behind guard 3.** We measured 45,181 leak-free rows across six
sports: correlations between every stored feature and the closing-line residual
run **0.02–0.06**, roughly 48 graded rules produced **not one** that clears a
bootstrap CI, and the apparent winners were five-of-six away-side features all
pointing the same way — the signature of one artefact, not six findings. So a
stat gap is real information about *the game* and close to zero information
about *the bet*. The layout must not imply otherwise.

---

## 9. Build order

1. **Attach rank/percentile to existing numbers.** Biggest readability win per
   unit of work; no new layout required.
2. **NHL goalie row + PP/PK pair.** Highest-value single addition in the stack.
3. **NFL/NCAAF off-vs-def pairs.** Data is 193/193 and 65/65 populated.
4. **NBA pace-led framing** before Oct 21.
5. **MLB lineup-vs-starter** for next season.

## 10. Two display bugs found while pulling these numbers

- **`nhl_game_context.xgf_per60` ships a raw float**:
  `2.9497779296207725`. Needs rounding at write or render — currently any UI
  reading it directly shows 16 decimal places.
- **NBA CHA and BKN both report `off_rating` 105.1** on the same game. Could be
  coincidence at one decimal, but it is worth confirming it is not a default
  being written for both sides.
