---
name: 2026 season roster facts I had wrong from stale training data
description: Player team changes from the 2025-2026 offseason that I missed. Trust the pipeline data + user corrections over my training-data recall.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
modified: 2026-09-28T18:50:27.620Z
---
My training cutoff was Jan 2026 — some 2025-26 offseason moves landed late and aren't in my recall. When in doubt, trust:
1. The pipeline data (HR Watch / lineup output / mlb_game_context)
2. User corrections

**Roster changes I had wrong:**

- **Pete Alonso → Baltimore Orioles** (2026 season). Was on Mets prior. Confirmed by user 2026-05-11 when I incorrectly flagged the HR Watch listing him as BAL as a "data bug."
- **Ranger Suarez → Boston Red Sox** (2026 season). Was on Phillies. 5/14: I attributed Luzardo's 10.8 ERA vs Boston (in `away_pitcher_vs_team_era`) to Suarez in a PHI/BOS card draft because my recall still had Suarez on Phillies. Created a fake "mastery fade" narrative; user caught it before public post. See [[feedback_verify_pitcher_attribution]] for the field-mapping rule.
- **Marcus Semien → New York Mets** (2026 season). Was on Rangers prior. 5/30: I saw Semien tagged on a MIA/NYM matchup, assumed it was a data bug, and saved a false `project_prop_attribution_audit` memo claiming the props pipeline had a player→matchup attribution bug. User immediately corrected me ("Smeien plays for mets, do better dont assume"). The memo was deleted. The pipeline was right; I was assuming again.

**NFL 2026 roster changes (I / subagents keep flagging as bugs):**

- **Kenneth Walker III → Kansas City Chiefs** (2026 season). Was on Seattle Seahawks prior. Andy has corrected me multiple times, most recently 2026-09-15 morning audit when the audit subagent flagged `player_team=KC` on Walker as a data-hygiene issue. **NOT A BUG.** Walker IS on KC. Do not flag.
- **Jaylen Waddle → Denver Broncos** (2026 season). Was on Miami Dolphins prior. Same 9/15 audit incorrectly flagged Waddle's `player_team=DEN`. **NOT A BUG.** Waddle IS on DEN. Do not flag.
- **Kyler Murray → Minnesota Vikings** (2026 season). Was on Arizona prior; **ARI's 2026 starter is Jacoby Brissett**. 9/28: while testing the QB injury gate I saw `MIN @ CHI away_qb_name = 'Kyler Murray'` and suspected a QB-attribution bug, because my recall had him on ARI. Checked both tables before saying anything — `nfl_game_context` and `nfl_injuries` independently agree Murray is MIN. **NOT A BUG.** Checking first is the only reason this didn't become another false alarm.

**How to apply (NFL specifically):** Before any audit or subagent flags an NFL player's team as suspect, cross-check `mlb_pipeline/nfl_current_depth_chart.json` (`per_team` sub-key) which is authoritatively scraped from nflverse depth charts. If a player appears there under a team, that team is correct. Do not assume prior-season teams. When in doubt: **the data is right, my training data is stale.**

**How to apply:** When a player appears on a team that surprises me, **default to assuming the data is right and my recall is stale**, not the other way around. Don't post "data bug" claims on roster questions without checking. If user contradicts, defer immediately.

**Why this matters:** Posting "Alonso shouldn't be on the Orioles" as a data-bug warning could have caused the user to publish a content piece doubting their own pipeline. False data-quality alarms are worse than missed signals.

**Followup:** If I notice other players appearing on unexpected teams in future cron output, ask the user before flagging as a bug. Better: just use the data as-is.
