---
name: project_sharp_scrapers_nfl_gap_911
description: cleatz + fadereport scrapers silently shipped 0 usable NFL signals since launch — 4 parser/query bugs + 2 team-name resolution gaps stacked. Fixed 2026-09-11 (71b53e01) to 42/42 matched.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-12T03:29:44.344Z
---

# Sharp scrapers — 6-bug NFL blackout at launch

Cleatz and fadereport are the two full-slate public-splits sources feeding
`classify_line_moves.py` (which the ensemble's sharp confluence signal
depends on). Both silently wrote 0 usable NFL signals from launch weekend
until 2026-09-11. Six independent bugs stacked into a full blackout — each
by itself would have zeroed the pipeline, and they compounded because
scraper output was non-fatal in cron so failures never surfaced.

## The six bugs (all landed in 71b53e01)

1. **fadereport ctx query narrow** — `_load_todays_games` used
   `game_date=eq.today`. NFL slate is Thu-Mon so on any Fri/Sat/Sun
   viewing 0 game_context rows loaded. Widened to 10-day window for
   NFL/NCAAF/NBA/NCAAB. Cleatz had already fixed this on 2026-09-10.

2. **fadereport RSC filter narrow** — `_matches_snap` filtered the 126-row
   RSC payload to games kicking off today ET. Same widening as #1.

3. **cleatz team-name regex** — cleatz.com's NFL page now emits pipe
   delimiters separated by whitespace after tag-strip (`| | NO Saints |
   @ | | DET Lions | |`). Old `\|` single-pipe separator matched zero
   sections. Widened to `(?:\|\s*)+`. MLB stays compatible.

4. **cleatz market navbar** — cleatz page now precedes each game with
   `| Spread | Total | Moneyline |` before the actual data section
   `| Spread | Consensus | | | DET Lions -7 | ...`. Old code took the
   first `| Spread |` as start and clipped end at the next navbar item,
   producing a 9-char header slice with zero % values. Detect the nav
   (next market within ~40 chars) and jump to the second occurrence.

5. **cleatz team resolver** — matched by last-word ('saints' vs
   nfl_game_context abbrev 'no') which never fires for NFL. Added
   first-word abbrev match + fuzzy substring fallback.

6. **Both scrapers — mascot alias enrichment** — fadereport gives just
   mascot ("Saints", "Cowboys"); cleatz's NY/LA shared-abbrev teams
   ("NY Jets" → NYJ, not NY). `_load_todays_games` now loads
   nfl_team_aliases and adds mascot-keyed lookup entries so mascot-only
   names resolve to the right game_id.

## Verified live 2026-09-11

- cleatz NFL: 14 games × 3 markets = 42 signals, **42/42 matched**
- fadereport NFL: 42/42 matched (was 0/42 before)
- cleatz NCAAF: 154 signals, 146/154 matched
- fadereport NCAAF: 249 signals, 229/249 matched

Downstream `classify_line_moves.py` joins by
`?game_id=eq.<odds_hash>&market=eq.<mkt>` — before these fixes the join
returned nothing for NFL despite the scrapers running each cron.

## Recompute impact

Running `recompute_nfl_primary_play.py --days 14` immediately after these
fixes changed 16 of 31 NFL primary_plays:
- WAS@PHI ML PRIME → rl/PHI -6/STRONG (spread pick now fires from sharps)
- GB@MIN MIN ML STRONG → GB ML STRONG (side flipped — sharps on GB)
- MIA@LV LV ML STRONG → MIA ML STRONG
- Several PRIME-tier picks demoted to COVERAGE because sharp signals
  contradicted them (this is the intended fade-vs-back correction)

## How to apply

Sharp scrapers run non-fatal in cron (`|| echo "failed"`). Add a
canary that surfaces "wrote 0 signals" as loud output — otherwise a
future page-format change will re-introduce the same class of blackout
and nobody notices until a user complains sharp confluence is missing.
Related: [[project_sharp_money_fade_808.md]],
[[project_signal_framework_821.md]],
[[project_nfl_prop_signal_gap_908.md]].
