---
name: project_nhl_buildout_927
description: "NHL went from 3 models and 4 rated teams to 5 models and 32; seven separate breakages found, all silent"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-28T02:44:52.230Z
---

Night of 2026-09-27, Andy: "make NHL ready, that is priority as NHL will
be nearly daily games to support non-football days."

**NHL's regular season opened 2026-09-29, not Oct 8.** Game ids encode
type at digits 4-6 (01 preseason / 02 regular). Two gates disagreed with
that and with each other: sport_registry said 10-08, and
season_gate.SEASON_WINDOWS had NHL = (10, 6) so is_sport_in_season was
FALSE all September. nhl_game_context exits on that check, so the sport
would have frozen as it opened. Both corrected.

**Seven breakages, every one silent.** The theme is a writer and a reader
disagreeing on a name, or a gate scoped to the wrong thing:

1. close_home_ml 0/59. enrich_market WROTE `home_ml_close` while line 588
   of the SAME FILE reads `close_home_ml`. close_total was the only
   market field with data — the only one whose name agreed. Consequence:
   _implied_wp always None, so ALL 51 picks were ml/HOME/STRONG/60, a
   default not a read. Now reads the prices nhl_odds_pull already writes
   correctly to nhl_game_results, joined on (date, home, away) = 59/59.
2. Team ratings for 4 of 32 clubs. MoneyPuck 2026 is HTTP 404 (published
   only once games are played) AND the context builder ranked teams
   within whatever subset played that night, so clubs read "1st of 4".
   New nhl_team_stats_pull.py: 13 stat keys x 32 teams, league_size 32.
   Andy: "use end of last year season until we have a week of games then
   shift" — switch is driven by SAMPLE SIZE (median 4+ games), not
   availability, because a one-game xGF/60 would reorder the league
   nightly.
3. No score projection, no Monte Carlo. Built nhl_projection.py. See
   [[project_nhl_projection_calibration_927]] for the three league
   invariants that caught it being wrong twice.
4. MC was rendered but had NO signal rows, and the ensemble only sees
   models through signal_sources — displayed, could not vote. 20260927b
   adds four, comparing sim probability to MARKET-IMPLIED rather than
   firing whenever the model likes a side (which just re-votes chalk).
5. **apply_mc_dissent_gate had NEVER fired outside MLB.** It read
   mc_p_home_win / mc_p_away_win; only MLB writes those. NFL, NCAAF and
   NHL all write mc_p_home. Fixed in the READER (one reader cannot drift;
   four writers already had). ALSO it ran BEFORE apply_ml_lr_override
   replaced the pick, and was scoped to ensemble_v2 which an LR pick is
   not — so the shipped pick was never checked against the sim. Now
   accepts lr_v1 and runs again after the override chain.
6. **Prop L10 graphs could never have worked.** backfill_prop_lookback
   resolves NHL players through an ESPN endpoint that returns HTTP 404,
   and ESPN's common/v3 search returns count 0 for McDavid/Matthews/
   MacKinnon. Replaced with api-web.nhle.com (new nhl_player_log.py):
   857 players across 32 rosters, game logs with opponentAbbrev and
   homeRoadFlag. Saves is DERIVED (shotsAgainst - goalsAgainst; the
   goalie log has no `saves`). Blocks and hits return NOTHING — the
   skater log lacks them and a fabricated series is worse than an empty
   graph.
7. No opening line ever captured (0/65). Same preserve-the-opener rule
   NFL learned on 09-16: mirror close->open only on first sighting.

**Still gated on the season starting, NOT broken:** market prices 29/65
(books post ~10 days out), props (`no_books_offering`, post 1-2 days
out), externals (3 sources ran 3/0 ok, 0 picks), money flow, and all
form/ATS/H2H splits. Verified each runs clean and returns empty.

**NHL model count is now 5** — Elo (projected_home_wp), goal projection
(the matchup), Monte Carlo, LR (models/nhl_ml_logreg.json, 4 features,
real and driving picks), ensemble. The stale comment in
nhl_game_context claiming "no NHL LR model trained yet" is wrong.

**Open calibration question for Andy:** the MC dissent STRONG threshold
is 0.48, so New Jersey shipped STRONG/64 with the sim at 0.488 — a
15-point model disagreement surviving. Tightening it is a calibration
call, not a bug fix.
