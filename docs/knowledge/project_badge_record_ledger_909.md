---
name: project-badge-record-ledger-909
description: "9/9 design queue — expand situational badge inventory + track per-badge historical record (Home Fav 35-20, Div Dog 32-20, Top Pass vs Bot Pass D 33-20). Football-first, sport-portable."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-09T16:43:53.550Z
---

🎯 9/9 design note. Extends [[project-sweat-badges-901]]. Each situational
badge on the game card should carry a rolling record beneath the chip
so users see WHY the badge is meaningful ("Home Fav 35-20 ATS L2yr"),
not just that the game qualifies. Football-first, portable to all sports.

**Why:** Currently the chip is a label with no credential. A badge that
reads "Home Fav 35-20 ATS" earns trust and doubles as content. Ties into
[[project-vault-match-901]] MOAT — same pipe, richer inventory. User
called this out on 9/9 seeing a lone "Home Fav" chip on a card and
asked what else could populate.

**How to apply:**
- Inventory below defines candidate badges (football-first).
- New table `sport_badge_records` stores hits/wins/losses/pushes per
  (sport, badge_key, window) — refreshed by cron off graded games.
- Card render pulls record and appends "W-L" or "W-L-P" beneath chip.
- Silent-hide badges with n < 30 (per [[feedback-sample-size-with-pct]]).

## UI placement

Situational section, above Recent Schedule, below Team Matchup
(Team Matchup deprecated — remove once next build ships). Chip stays
same size; record renders as small caption line under the chip label
("Home Fav · 35-20 ATS L2yr, 63.6%"). Cap 2 badges per card.

## Football-first badge inventory

**Alignment (already shipped Home Fav):**
- Home Fav / Home Dog / Road Fav / Road Dog
- Div Fav / Div Dog
- Div Home Dog (already in Phase 1, richest single angle NFL)

**Matchup edges (needs cached team_stat_rank matview):**
- Top-10 Pass Off vs Bot-10 Pass D
- Top-10 Rush Off vs Bot-10 Rush D
- Top-10 Scoring D vs Bot-10 Scoring Off
- Elite Pass Rush vs Bot-10 OL (sack% or PFF proxy)
- Top-10 TO+ vs Bot-10 TO-
- Red Zone: Top-10 RZ TD% vs Bot-10 RZ Def
- 3rd Down: Top-10 conv vs Bot-10 3rd Def
- Explosive Play edge (top vs bot explosive%)

**Situational spots:**
- Short Week (TNF)
- Off Bye (14+ days rest)
- Rest Edge (Δ days_rest ≥ 3)
- Prime Time (SNF/MNF/TNF)
- Cross-Country (3+ tz shift eastward early kick)
- Altitude (Denver home)
- Bounce Back (last game blowout L or 2+ L streak — already Phase 2 "Get Right")
- Regression (last game blowout W by 14+ ATS cover)
- Trap Spot (fav coming off marquee W, headed into div game next)
- Look-Ahead (fav vs weak opp, div rival next week)
- Revenge (same opponent last matchup was L, esp playoff L)

**Weather (already shipped, could add records):**
- Cold ≤ 32°F
- Wind ≥ 15 mph
- Rain/Snow ≥ 40% precip
- Dome team on road outdoor

**QB / roster:**
- Backup QB Starting
- Rookie QB Road
- QB vs Former Team
- HC vs Former Team

**Line/Market:**
- Reverse Line Movement
- Steam Move (already covered by Sharp $ triple-confirmed)
- Public Fade (≥70% public other side, line hasn't moved)
- Overnight Line Move (≥1 pt shift open→current)

**Trends:**
- ATS Heater (5+ ATS L5)
- ATS Ice (0-5 ATS L5)
- Over Streak (5+)
- Under Streak (5+)

## Cross-sport port targets

- MLB: Home Fav, Road Dog, Div Dog, Ace vs Bum (xERA gap ≥ 1.5), Bounce
  Back, Bullpen Edge (top-10 bpen ERA vs bot-10), LHP vs RHH Split team
- NBA: Home Fav, Rest Edge, B2B Fade, Pace Clash, Top-10 O vs Bot-10 D
- NCAAB: Home, Conf Dog, Tempo Clash, KenPom (call "efficiency") gap
- NHL: Goalie Edge (Elo gap ≥ 30), B2B Fade, Home Dog, Div Dog
- NCAAF: Home Fav, SP+ Gap, Ranked-vs-Unranked, FBS-vs-FCS (auto-fade)

## Data plumbing

**New table `sport_badge_records`:**
```
sport            text
badge_key        text        -- 'home_fav_ats', 'div_home_dog_ats', etc.
window           text        -- 'l2yr', 'ytd', 'lifetime'
wins             int
losses           int
pushes           int
units_won        numeric
sample_n         int
updated_at       timestamptz
PRIMARY KEY (sport, badge_key, window)
```

**Backfill:** run once against graded games (`daily_side_results` +
`daily_total_results`). For each historical game, evaluate each badge
rule against the closing context row and increment counters based on
the ATS/OU outcome. Reuse the same rule functions that the LIVE badge
gate uses — one source of truth.

**Refresh cron:** nightly append of newly-graded games. Rolling windows
recompute cheap since counters are small.

**Attach to card:** context row already carries all fields needed
(close_spread, div_game, days_rest, weather, rest disparity, team ranks
via matview). Cron writes `badges: [{key, label, record}]` onto ctx or
a sibling table.

## Guardrails

- Hide record if n < 30 (chip still renders; record line silent)
- Never show all-time win% below 45% (that's a fade badge, not a back
  badge — surface it in a "Fade Watch" section instead)
- Cap 2 badges per card, priority: sport-unique > matchup edge > alignment
- Emoji + short label + record caption; ≤ 14 chars label, ≤ 22 chars
  record line

## Queue position

Not launch-blocking. Slot after v1.0.1 client punchlist
[[project-v1-0-1-client-priorities]] wraps. Backfill can run in
parallel with client ship since it's server-only. Est. 2-3 sessions:
(1) inventory rules module + backfill, (2) matview + cron, (3) card
render + hide/show polish.
