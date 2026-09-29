---
name: project_externals_are_mostly_money_flow_925
description: "Only 2 of 7 NFL 'externals' are real handicapper picks (pickswise, pickdawgz). The rest are money-flow splits or model win-prob that we convert into pseudo-picks — so their 'records' are signal records, not tout records."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-25T05:27:13.564Z
---

# Most "externals" are not picks

Andy 2026-09-25: "I don't think OC does actual picks just moneyflow?" —
correct, and it is true of most of the list. Verified from
`external_picks.raw_text` per source:

| source | raw_text shape | what it is |
|---|---|---|
| oddscrowd | "Spread: AWAY money 49%/bets 65%…" | money/bets splits |
| scoresandodds | "TOTAL: OVER money 24%/bets 25%" | money/bets splits |
| covers | "Public spread consensus: 69% away" | public bet% |
| action | "Action bet%: Giants 69%" | public bet% |
| dimers | "Dimers wp: Rams 75.4%" | their model's win prob |
| **pickswise** | "Pickswise: Dallas Cowboys RL 3.5 (-108)" | **a real pick** |
| **pickdawgz** | "PickDawgz: Atlanta Falcons +6" | **a real pick** |

We derive `pick_side` from whichever side the money/bets/win-prob favours,
then grade it like a tout pick.

**Why this matters:** a hit rate on those five is the record of a *strategy*
("follow the money-majority side", "back the higher win-prob team"), not a
handicapper's record. dimers' 66.7% is close to "back the model favourite",
i.e. chalk. Presenting these under a Handicappers heading overstates what
they are. Note [[project_sharp_money_fade_808]] concluded OC sharp money is a
FADE, so a naive follow-side record is arguably backwards.

**How to apply:** when reporting external records, separate
*money-flow/derived* from *actual picks*. Never cite a blended "externals
record" as tout performance. Real-pick sample is tiny — pickswise n=19,
pickdawgz n=10 — so we effectively have NO proven tout record for NFL yet.

## OddsCrowd state 2026-09-25

NFL has **nothing** from OC — not picks, not money flow.
`nfl_game_context.oddscrowd_snapshot` went 14/14 games on 09-20 to **0** on
every game after, so the sharp-split signals that depend on it are silent for
NFL. NCAAF dead since 09-19. **MLB still alive** (last 09-24).

Cause: OddsCrowd changed three things at once —
1. listing is now client-rendered (625KB of HTML, zero matchup links; needs
   `render_page(..., html=True)`, added 09-25),
2. URL date slug went numeric `9-25-2026` -> month-name `september-25-2026`,
3. `/games/upcoming/football` now returns **MLS soccer**, not gridiron.

MLB survives because `/upcoming/baseball` is untouched. Recovering NFL means
rediscovering their URL scheme — a rework, not a patch.

**Money flow overall is NOT down.** For the 09-27 slate all 14 games have
cleatz, fadereport, fadethepublic AND scoresandodds. Only OC is missing.
