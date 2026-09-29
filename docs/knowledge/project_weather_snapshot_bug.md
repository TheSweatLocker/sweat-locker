---
name: project-weather-snapshot-bug
description: "get_weather() pulls /data/2.5/weather (current conditions) at morning cron — not gametime; cold-weather override on day games is real, on night games is stale"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

`mlb_pipeline/game_context.py:get_weather()` calls OpenWeather's **`/data/2.5/weather`** endpoint, which returns **current observed conditions**, not a gametime forecast. The morning cron resolves dawn temperature at the venue and that single snapshot feeds:
- `weather_adj` in v3 total projection
- `temperature` argument to `calc_nrfi_score`
- Sweat-dim UNDER bias (cold-weather term)
- Jerry projection input

For evening / night games this is **systematically wrong**. 5/31 ARI @ SEA case: morning cron stored 44°F (Seattle 7am PT dawn). First pitch is 9:40pm ET / 6:40pm PT → actual gametime closer to 60-65°F. The cold-weather UNDER override on that game was leaning on stale data while disagreeing with both v4 (8.8) and Jerry (9.02).

**Why this matters now:**
- User flagged 5/31: "the weather in Seattle will be considerably warmer come gametime, not liking how much weather overides currently … if that is the basis of disagreeing with two models i dont love it"
- It's a credibility issue, not just an accuracy one. The override is doing real work in scoring and I can't justify it from a 12-hour-old reading.

**How to apply:**
- Pre-fix: do NOT lean on cold-weather UNDER overrides in card writeups for night-game venues unless v3 / v4 / Jerry agree on the UNDER side independently. The signal alone is not card-grade until the fetch is gametime-resolved.
- Fix options (queued, not yet shipped):
  1. Switch to OpenWeather `/data/2.5/forecast` with `first_pitch_utc` lookup → pick the 3-hour bucket nearest game time
  2. OR move weather fetch to the 4pm ET cron (closer to first pitch for evening slate) so `/weather` (current) is roughly correct
  3. Stamp `weather_at` timestamp on the row so downstream can detect stale snapshots
- Related: [[project_may30_potd_bugs_and_gap]]
